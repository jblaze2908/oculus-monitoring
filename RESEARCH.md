# Server Monitoring — Pain Points & Landscape Research (July 2026)

Three research streams: homelab/self-hosted postmortems, production outage analyses,
and the SaaS + open-source product landscape. Collated below.

---

## 1. The collated pain map — what actually breaks servers

Merged from homelab postmortems and production outage data, ranked by how often each
theme recurs and how badly it hurts. The striking result: **the same ~6 themes dominate
both a Raspberry Pi in a closet and AWS us-east-1.**

### Tier 1 — the killers

**1. Backups that were never restore-tested.**
The #1 recurring theme in both worlds. GitLab 2017 (18h outage, backup-failure emails
silently DMARC-rejected for months), Matrix.org 2025 (WAL-archiving silently broken
exactly when needed), JournalSpace (company killed — RAID was the "backup").
→ *Monitor restore success, not backup exit codes. Run periodic restore drills.*

**2. The alerting path itself fails silently.**
GitLab's backup alerts were bouncing for months. Homelabbers' SMS alert bridges die
invisibly. "A monitor that fails silently is worse than no monitor — it creates false
confidence." → *Dead-man's-switch on your own alert pipeline (heartbeat that alerts
when it STOPS arriving).*

**3. Watching the wrong signal — "up" but broken.**
Cross-cutting cause in the worst production postmortems: health check green while
functionality dead. Expired cert with 200s still flowing, DNS resolving stale, service
running but not answering. → *Probe the function (real DNS query, real HTTP fetch),
not the process.*

**4. Expiry class: certificates, tokens, domains.**
Cert expiry alone: Ericsson (11 telcos down), Microsoft Teams, Spotify — average
cert-expiry outage >5h and $500K+. Tokens/API keys worse: no standard expiry signal at
all. → *Days-until-expiry as a first-class monitored metric, alert at 30/14/7/1 days.*

### Tier 2 — the slow killers

**5. Disk: three distinct failure modes, usually only one is watched.**
- Full (watch rate-of-change, not just %— runaway logs outrun threshold alerts)
- Inode/FD/port exhaustion ("disk fine but writes failing" — almost never on default dashboards)
- Hardware death (SMART pending sectors; usage % gives zero warning)
Homelab flavor: storage growth always underestimated ("budget 2-3x").

**6. Power + thermal.**
Power loss mid-write corrupts filesystems (top cause of Pi SD death); dying fans /
dust / dried paste → seasonal thermal shutdowns ("servers that reboot in summer").
UPS-less setups discover this the hard way. → *On-battery transitions, uptime resets
(unexpected reboot log), temp trends + throttle events.*

**7. Updates/config changes breaking things.**
Production: config changes = the single most common trigger of severe outages (~50%
in postmortem samples; CrowdStrike 2024). Homelab: Watchtower auto-pulls a broken
image, stack silently dies. → *Pin versions; verify health after every update; track
"deploys vs incidents".*

### Tier 3 — the structural mistakes

**8. DNS as single point of failure.** "Pi-hole ate my internet" is a genre. Your
AdGuard box IS this. Real failover needs keepalived/VRRP, not a second DHCP DNS entry.
**9. Security exposure.** Port-forwarded services with default creds, no MFA, no
fail2ban; SSH brute force. Named #1 beginner regret.
**10. Converged storage SPOF + reachable backups.** One NAS backing everything
(11-day outage while owner on holiday); ransomware encrypting primary AND backup
because backup was just another network share (CloudNordic: total loss). 3-2-1 rule.
**11. Error handling / cascades (prod-specific).** 92% of severe failures = errors
handled incorrectly; retry storms (AWS 2025: 500→15,000 QPS in 1s).
**12. Learning on mission-critical data.** Practice on throwaway services first.

---

## 2. SaaS landscape (what money buys)

| Platform | Killer feature | Floor |
|---|---|---|
| Datadog | security↔observability correlation, 800 integrations | free 5 hosts/1-day; $15/host/mo |
| New Relic | 100GB/mo ingest free forever — best major free tier | $0.40/GB overage |
| Grafana Cloud | hosted Prometheus/Loki stack, generous free tier | free: 10k series/50GB logs |
| Better Stack | eBPF zero-code APM + AI root-cause | free 10 monitors; $29/mo |
| Netdata Cloud | per-metric ML anomaly detection (18-model consensus) | free 5 nodes; $4.50/node/mo |
| UptimeRobot | 50 free monitors — best free entry | $9/mo |
| Pingdom | global synthetic + RUM | $10/mo, no free tier |
| Site24x7 | all-in-one breadth (web+network+cloud) | ~$9/mo |
| PRTG | deep SNMP/network-device monitoring | free 100 sensors |
| HetrixTools | RBL blacklist monitoring (unique niche) | free 15+15 monitors |
| Checkmk Cloud | 2000+ check plugins | ~$240/mo — enterprise |

What SaaS buys that self-hosted can't: **external vantage points** (probes from outside
your network — detects "my ISP/wifi died"), long retention with no ops, escalation
(SMS/voice), and RUM. For a home server, the free tiers of UptimeRobot or HetrixTools
cover the external-probe gap at $0.

## 3. Open-source landscape (what exists already)

| Project | Covers | Alerting | Stars |
|---|---|---|---|
| Uptime Kuma | probes: HTTP/TCP/DNS/docker/SSL-expiry, 90+ notif channels | ✅ | 89k |
| Netdata | per-second everything + ML anomalies | ✅ | 80k |
| Glances (our base) | resource metrics via REST | ❌ none | 33k |
| Homepage | service start-page w/ widgets | ❌ | 32k |
| **Beszel** | metrics + docker stats + **history** + hub/agent, 6MB RAM | ✅ | 24k |
| Dozzle | live docker log tail | ❌ | 14k |
| Gatus | config-as-code probes | ✅ | 12k |
| Zabbix / Checkmk | enterprise NMS | ✅ | heavy |
| Scrutiny | SMART disk health + failure-rate thresholds | ✅ | ~8k |
| Healthchecks.io | cron dead-man's-switch | ✅ | — |

**r/selfhosted 2025-26 consensus for one docker home server: Beszel + Uptime Kuma
(+ Dozzle for logs).** Grafana/Prometheus reserved for those who outgrow that.

## 4. Verdict vs our build

- **Beszel beats Oculus on ops substance**: history persistence, threshold alerting,
  multi-host, 6MB footprint. Our build wins on UI density and breadth-in-one-page
  (battery/wifi/GPU/processes/TERM view — Beszel has none of those).
- **Glances' fatal flaw as a base: zero alerting.** Everything we built is a
  glass cockpit with no warning lights that reach your phone.
- **The genuine gap nothing single-container fills**: metrics + persistence +
  alerting + external-ish probes in ONE lightweight container. Everyone composes
  2-3 tools (Beszel + Kuma + Scrutiny). Oculus + (a) SQLite persistence,
  (b) threshold alerts → ntfy/Telegram, (c) probe engine (DNS query to AdGuard,
  HTTP checks, cert expiry, WAN ping) would sit in that gap.

## 5. What this means for the master-server box, concretely

Ranked next steps, mapped to the pain map:
1. **Alerting push (ntfy/Telegram) + dead-man heartbeat** — kills pain #2, makes everything else matter.
2. **Service probes**: DNS query against AdGuard, HTTP checks, WAN ping/latency — kills #3, #8.
3. **SMART via smartctl/Scrutiny** — kills #5c (old laptop disk = highest hardware risk).
4. **Persistence (SQLite ring buffer)** — unlocks trends: disk growth rate, temp creep, reboot/on-battery event log (#5, #6).
5. **SSH auth-failure + pending-updates panel** — #9.
6. **Restore-tested backups** — nothing to monitor yet: the box has NO backups. Pain #1 says fix that before polishing dashboards.
