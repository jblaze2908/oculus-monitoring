FROM python:3.12-alpine3.21
# smartmontools: SMART disk health for the monitor thread
RUN apk add --no-cache smartmontools
WORKDIR /app
COPY server.py monitor.py index.html ./
# Root required: smartctl needs raw device access (SYS_RAWIO + /dev/sdX),
# docker.sock queries need socket access. Mirrors the upstream glances model.
EXPOSE 8080
CMD ["python", "server.py"]
