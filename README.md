# Oculus Monitoring

Single-file server dashboard on top of the [Glances](https://nicolargo.github.io/glances/)
REST API. Dark, calm UI: CPU / memory / swap / battery (with charging state), per-core
sparklines, network + disk I/O charts, storage, thermals, Wi-Fi signal, GPU, docker
containers, glances alert log, and an htop-style TERM view.

![stack](https://img.shields.io/badge/stack-glances%20%2B%20python%20stdlib-blue)

## Architecture

```
browser ──:8080──> dashboard container (python stdlib)
                     ├── serves index.html
                     └── proxies /api/* ──> glances :61208 (loopback only)
```

- `index.html` — the whole UI. Vanilla JS + canvas, zero external requests, polls every 2 s.
- `server.py` — stdlib HTTP server: static + same-origin API proxy (no CORS).
- `Dockerfile` — python alpine, non-root.
- `docker-compose.yml` — glances (host net/pid, docker.sock ro) + dashboard.

## Run

```bash
docker compose up -d
# dashboard: http://<host>:8080
```

Env vars for the dashboard container: `GLANCES_URL` (default `http://127.0.0.1:61208`),
`PORT` (default `8080`).

## Images

- Public: `jblaze2908/oculus-monitoring` (`latest` + semver tags)
- CI: pushing to `main` publishes `latest`; pushing a `v*.*.*` tag publishes that version.

## CI secrets

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token (write scope) |
