#!/bin/sh
# glances collector on loopback (never LAN-exposed), dashboard proxy in foreground.
# If the proxy dies the container exits and docker restarts it; if glances dies
# the dashboard shows STALE and the HEALTHCHECK recycles the container.
glances -w -B 127.0.0.1 -p 61208 --disable-webui &
exec python /app/server.py
