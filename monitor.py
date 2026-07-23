"""Oculus monitor: collectors, history persistence, rules engine, ntfy alerts.

Runs as a daemon thread inside the dashboard container (started by server.py).
Collects from the local Glances API + smartctl + docker.sock + a raw DNS probe,
persists history/events to SQLite, and pushes alerts to an ntfy topic.
"""
import json
import os
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.request

GLANCES = os.environ.get("GLANCES_URL", "http://127.0.0.1:61208").rstrip("/")
NTFY_URL = os.environ.get("NTFY_URL", "")  # e.g. https://ntfy.sh/oculus-xxxx ; empty = alerts logged only
DB_PATH = os.environ.get("OCULUS_DB", "/data/oculus.db")
SMART_DEVICE = os.environ.get("SMART_DEVICE", "/dev/sda")
DNS_SERVER = os.environ.get("DNS_CHECK", "127.0.0.1")  # AdGuard listens on the host
POLL_S = 30
SMART_EVERY = 30          # every 30 cycles = 15 min
COOLDOWN_S = 30 * 60      # min gap between repeat notifications per rule
HISTORY_KEEP_S = 30 * 24 * 3600

_lock = threading.Lock()
snapshot: dict = {"smart": None, "dns": None, "alerts": {}, "restarts": {}, "started": time.time()}


# ---------- integrations ----------

def _glances(path: str):
    with urllib.request.urlopen(f"{GLANCES}/api/4/{path}", timeout=10) as r:
        return json.load(r)


def notify(title: str, body: str, priority: str = "default", tags: str = "") -> None:
    if not NTFY_URL:
        return
    try:
        req = urllib.request.Request(NTFY_URL, data=body.encode(), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", tags)
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # never let a broken notify kill the monitor loop


def dns_probe(server: str, name: str = "example.com", timeout: float = 3.0):
    """Raw UDP DNS A query; returns latency ms or None. Stdlib only."""
    q = struct.pack(">HHHHHH", 0x4F43, 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.time()
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
        ms = (time.time() - t0) * 1000
        rcode = data[3] & 0x0F
        return round(ms, 1) if rcode == 0 else None
    except Exception:
        return None
    finally:
        s.close()


def smart_read(device: str):
    """smartctl health + key wear attributes. Returns dict or None."""
    try:
        out = subprocess.run(
            ["smartctl", "-H", "-A", "-j", device],
            capture_output=True, timeout=30,
        ).stdout
        d = json.loads(out)
        attrs = {}
        for a in d.get("ata_smart_attributes", {}).get("table", []):
            if a.get("name") in ("Reallocated_Sector_Ct", "Current_Pending_Sector",
                                 "Offline_Uncorrectable", "Wear_Leveling_Count",
                                 "Media_Wearout_Indicator", "Temperature_Celsius",
                                 "Power_On_Hours", "SSD_Life_Left"):
                attrs[a["name"]] = a.get("raw", {}).get("value")
        nvme = d.get("nvme_smart_health_information_log") or {}
        if nvme:
            attrs.update({"percentage_used": nvme.get("percentage_used"),
                          "media_errors": nvme.get("media_errors"),
                          "temperature": nvme.get("temperature")})
        return {
            "device": device,
            "passed": bool(d.get("smart_status", {}).get("passed")),
            "model": d.get("model_name"),
            "attrs": attrs,
            "ts": time.time(),
        }
    except Exception:
        return None


def _docker_get(path: str):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect("/var/run/docker.sock")
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: docker\r\n\r\n".encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.partition(b"\r\n\r\n")[2])
    finally:
        s.close()


def docker_restarts():
    """name -> {restarts, status}; needs /var/run/docker.sock mounted ro."""
    out = {}
    try:
        for c in _docker_get("/containers/json?all=1"):
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            detail = _docker_get(f"/containers/{c['Id']}/json")
            out[name] = {"restarts": detail.get("RestartCount", 0),
                         "status": c.get("State", "?")}
    except Exception:
        pass
    return out


# ---------- persistence ----------

def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS history(
        ts REAL, cpu REAL, mem REAL, disk_pct REAL, disk_used REAL,
        temp_max REAL, bat REAL, bat_status TEXT, net_down REAL, net_up REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS events(
        ts REAL, kind TEXT, detail TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS hist_ts ON history(ts)")
    return con


def add_event(con: sqlite3.Connection, kind: str, detail: str) -> None:
    con.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), kind, detail))
    con.commit()


def disk_growth(con: sqlite3.Connection):
    """bytes/day over last 7 days + days-until-full; None if not enough data."""
    rows = con.execute(
        "SELECT ts, disk_used, disk_pct FROM history WHERE ts > ? ORDER BY ts",
        (time.time() - 7 * 24 * 3600,)).fetchall()
    if len(rows) < 20:
        return None
    (t0, u0, _), (t1, u1, p1) = rows[0], rows[-1]
    span_days = (t1 - t0) / 86400
    if span_days < 0.5:
        return None
    per_day = (u1 - u0) / span_days
    if per_day <= 0 or p1 is None:
        return {"bytes_per_day": per_day, "days_to_full": None}
    free = u1 / (p1 / 100) - u1 if p1 > 0 else 0
    return {"bytes_per_day": per_day, "days_to_full": round(free / per_day, 1)}


# ---------- rules engine ----------

class Rule:
    def __init__(self, key, title, priority, tags):
        self.key, self.title, self.priority, self.tags = key, title, priority, tags
        self.active = False
        self.last_sent = 0.0

    def update(self, firing: bool, body: str, con: sqlite3.Connection) -> None:
        now = time.time()
        if firing and (not self.active or now - self.last_sent > COOLDOWN_S):
            notify(self.title, body, self.priority, self.tags)
            add_event(con, "alert", f"{self.title}: {body}")
            self.last_sent = now
        elif self.active and not firing:
            notify(f"Resolved: {self.title}", body or "back to normal", "default", "white_check_mark")
            add_event(con, "resolved", self.title)
        self.active = firing


RULES = {k: Rule(k, t, p, g) for k, t, p, g in [
    ("power",     "Power lost — on battery", "urgent",  "electric_plug,warning"),
    ("bat_low",   "Battery low",             "urgent",  "battery,rotating_light"),
    ("disk",      "Disk almost full",        "high",    "floppy_disk,warning"),
    ("temp",      "Overheating",             "high",    "thermometer,fire"),
    ("mem",       "Memory pressure high",    "high",    "brain,warning"),
    ("smart",     "Disk health (SMART)",     "urgent",  "floppy_disk,skull"),
    ("dns",       "DNS not answering",       "high",    "globe_with_meridians,x"),
    ("container", "Container down",          "high",    "package,x"),
]}


# ---------- main loop ----------

def _cycle(con: sqlite3.Connection, state: dict, n: int) -> None:
    ql = _glances("quicklook")
    sensors = _glances("sensors")
    fs = [f for f in _glances("fs") if f.get("size", 0) >= 2**30]
    uptime_s = _uptime_seconds()
    bat = next((s for s in sensors if s.get("type") == "battery"), None)
    temps = [s for s in sensors if s.get("unit") == "C"]
    net = [n_ for n_ in _glances("network") if n_.get("interface_name") != "lo"]
    down = sum(n_.get("bytes_recv_rate_per_sec") or 0 for n_ in net)
    up = sum(n_.get("bytes_sent_rate_per_sec") or 0 for n_ in net)

    root = next((f for f in fs if f["mnt_point"] == "/"), fs[0] if fs else None)
    temp_max = max((t["value"] for t in temps), default=None)
    bat_val = bat.get("value") if bat else None
    bat_st = (bat.get("status") or "").lower() if bat else ""

    con.execute("INSERT INTO history VALUES(?,?,?,?,?,?,?,?,?,?)", (
        time.time(), ql.get("cpu"), ql.get("mem"),
        root and root.get("percent"), root and root.get("used"),
        temp_max, bat_val, bat_st, down, up))
    con.execute("DELETE FROM history WHERE ts < ?", (time.time() - HISTORY_KEEP_S,))
    con.commit()

    # reboot detection
    if state.get("uptime") and uptime_s < state["uptime"] - POLL_S:
        add_event(con, "reboot", f"unexpected reboot — uptime reset to {uptime_s:.0f}s")
        notify("Server rebooted", "uptime counter reset — was this expected?", "high", "arrows_counterclockwise")
    state["uptime"] = uptime_s

    # power transitions logged as events (rule handles the notification)
    if bat and state.get("bat_status") not in (None, bat_st):
        add_event(con, "power", f"battery status: {state['bat_status']} -> {bat_st} at {bat_val}%")
    state["bat_status"] = bat_st

    discharging = bat_st == "discharging"
    RULES["power"].update(discharging,
                          f"battery {bat_val}% and discharging — charger unplugged or power cut", con)
    RULES["bat_low"].update(discharging and bat_val is not None and bat_val < 20,
                            f"battery at {bat_val}%, discharging — shut down soon", con)
    RULES["disk"].update(bool(root and root.get("percent", 0) > 85),
                         root and f"{root['mnt_point']} at {root['percent']:.0f}%", con)
    hot = [t for t in temps if t["value"] >= (t.get("warning") or 80)]
    RULES["temp"].update(bool(hot),
                         ", ".join(f"{t['label']} {t['value']}C" for t in hot) or "", con)
    RULES["mem"].update(ql.get("mem", 0) > 90, f"memory at {ql.get('mem')}%", con)

    # dns
    ms = dns_probe(DNS_SERVER)
    fails = state.get("dns_fails", 0)
    state["dns_fails"] = 0 if ms is not None else fails + 1
    RULES["dns"].update(state["dns_fails"] >= 3,
                        f"no answer from {DNS_SERVER}:53 ({state['dns_fails']} consecutive probes)", con)

    # containers: restarts + down
    ctrs = docker_restarts()
    prev = state.get("ctrs", {})
    downs, restarted = [], []
    for name, c in ctrs.items():
        p = prev.get(name)
        if p and c["restarts"] > p["restarts"]:
            restarted.append(f"{name} (x{c['restarts']})")
        if p and p["status"] == "running" and c["status"] != "running":
            downs.append(f"{name} -> {c['status']}")
    if restarted:
        add_event(con, "container", "restarted: " + ", ".join(restarted))
        notify("Container restarted", ", ".join(restarted), "high", "package,arrows_counterclockwise")
    RULES["container"].update(bool(downs), ", ".join(downs), con)
    state["ctrs"] = ctrs

    # smart (every 15 min)
    smart = snapshot["smart"]
    if n % SMART_EVERY == 0:
        smart = smart_read(SMART_DEVICE) or smart
        if smart:
            bad = (not smart["passed"]) or any(
                (smart["attrs"].get(k) or 0) > 0
                for k in ("Reallocated_Sector_Ct", "Current_Pending_Sector",
                          "Offline_Uncorrectable", "media_errors"))
            RULES["smart"].update(bad,
                                  f"{smart.get('model')}: passed={smart['passed']} attrs={smart['attrs']}", con)

    with _lock:
        snapshot.update({
            "smart": smart,
            "dns": {"server": DNS_SERVER, "latency_ms": ms,
                    "ok": ms is not None, "fails": state["dns_fails"]},
            "restarts": ctrs,
            "alerts": {k: r.active for k, r in RULES.items() if r.active},
            "growth": disk_growth(con),
        })


def _uptime_seconds() -> float:
    u = _glances("uptime")  # "H:MM:SS" or "N days, H:MM:SS"
    days = 0
    if "," in u:
        days = int(u.split()[0])
        u = u.split(",")[1].strip()
    h, m, s = (int(x) for x in u.split(":"))
    return days * 86400 + h * 3600 + m * 60 + s


def api_status() -> dict:
    con = _db()
    try:
        events = con.execute(
            "SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 20").fetchall()
        with _lock:
            out = dict(snapshot)
        out["events"] = [{"ts": t, "kind": k, "detail": d} for t, k, d in events]
        return out
    finally:
        con.close()


def api_history(hours: int = 24) -> list:
    con = _db()
    try:
        rows = con.execute(
            "SELECT ts,cpu,mem,disk_pct,temp_max,bat,net_down,net_up FROM history "
            "WHERE ts > ? ORDER BY ts", (time.time() - hours * 3600,)).fetchall()
        keys = ["ts", "cpu", "mem", "disk_pct", "temp_max", "bat", "down", "up"]
        return [dict(zip(keys, r)) for r in rows]
    finally:
        con.close()


def _loop() -> None:
    con = _db()
    add_event(con, "monitor", "oculus monitor started")
    notify("Oculus monitor online",
           "alerts armed: power, battery, disk, temp, memory, SMART, DNS, containers",
           "default", "eye")
    state: dict = {}
    n = 0
    while True:
        try:
            _cycle(con, state, n)
        except Exception:
            pass  # glances restarting etc — retry next tick
        n += 1
        time.sleep(POLL_S)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="oculus-monitor").start()
