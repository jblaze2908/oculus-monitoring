"""Server dashboard: serves the static UI, proxies /api/* to local Glances,
and exposes the Oculus monitor (alerts/SMART/DNS/history) at /oculus/*.

Stdlib only.
"""
import http.server
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import monitor

GLANCES = os.environ.get("GLANCES_URL", "http://127.0.0.1:61208").rstrip("/")
ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8080"))

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path.startswith("/oculus/"):
            self._oculus()
        elif self.path.startswith("/api/"):
            self._proxy()
        elif self.path in ("/", "/index.html"):
            self._serve_index()
        else:
            self.send_error(404)

    def _oculus(self) -> None:
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/oculus/status":
                data = monitor.api_status()
            elif url.path == "/oculus/history":
                hours = int(urllib.parse.parse_qs(url.query).get("hours", ["24"])[0])
                data = monitor.api_history(min(hours, 24 * 30))
            else:
                self.send_error(404)
                return
            self._respond(200, json.dumps(data).encode(), "application/json")
        except Exception:
            self.send_error(500, "monitor error")

    def _proxy(self) -> None:
        if ".." in self.path:
            self.send_error(400)
            return
        try:
            with urllib.request.urlopen(GLANCES + self.path, timeout=5) as r:
                body = r.read()
                self._respond(200, body, "application/json")
        except urllib.error.HTTPError as e:
            self._respond(e.code, e.read(), "application/json")
        except Exception:
            self.send_error(502, "glances unreachable")

    def _serve_index(self) -> None:
        try:
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self._respond(200, f.read(), "text/html; charset=utf-8")
        except OSError:
            self.send_error(500)

    def _respond(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep journal quiet
        pass

if __name__ == "__main__":
    monitor.start()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
