#!/usr/bin/env bash
# Manual deploy: build on the server, restart the stack, push to Docker Hub.
# Usage: ./deploy.sh <version>   e.g. ./deploy.sh 1.3
set -euo pipefail

VERSION="${1:?usage: ./deploy.sh <version>}"
HOST="jblaze2908@192.168.1.10"
DIR="/opt/serverdash"
IMAGE="jblaze2908/oculus-monitoring"

echo "==> shipping files to $HOST:$DIR"
tar cf - Dockerfile docker-compose.yml server.py monitor.py index.html \
  | ssh "$HOST" "mkdir -p /tmp/oculus-deploy && tar xf - -C /tmp/oculus-deploy"

ssh -t "$HOST" "sudo bash -c '
  set -euo pipefail
  cp /tmp/oculus-deploy/* $DIR/ && rm -rf /tmp/oculus-deploy
  cd $DIR
  sed -i \"s|image: $IMAGE:.*|image: $IMAGE:$VERSION|\" docker-compose.yml
  docker compose up -d --build
  docker tag $IMAGE:$VERSION $IMAGE:latest
  docker push $IMAGE:$VERSION
  docker push $IMAGE:latest
  sleep 5
  curl -sf -o /dev/null http://localhost:8080/ && echo \"DEPLOYED $VERSION — dashboard healthy\"
'"
