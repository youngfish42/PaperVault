#!/bin/bash
# Blue-green deploy: boot the new container on the idle port, wait for health,
# switch nginx, then retire the old container. Serves traffic throughout.
set -euo pipefail
cd /srv/papervault

IMAGE="ghcr.io/youngfish42/papervault:latest"
NGINX_CONF=/etc/nginx/conf.d/papervault.top.conf

current=$(grep -o "proxy_pass http://127.0.0.1:[0-9]*" "$NGINX_CONF" | grep -o "[0-9]*$")
if [ "$current" = "5001" ]; then next=5002; else next=5001; fi
echo "current port: $current -> next port: $next"

if [ "${SKIP_PULL:-0}" != "1" ]; then
  docker pull "$IMAGE" 2>/dev/null || {
    echo "${GITHUB_TOKEN:?need ghcr token}" | docker login ghcr.io -u youngfish42 --password-stdin
    docker pull "$IMAGE"
  }
fi

# Idle slot cleanup, then boot the new container on the idle port.
docker rm -f "papervault-$next" 2>/dev/null || true
docker run -d --name "papervault-$next" \
  --restart unless-stopped \
  --env-file .env \
  -p "$next:5001" \
  -v /srv/papervault/cache-$next:/app/cache \
  "$IMAGE" >/dev/null

echo "waiting for papervault-$next to become healthy (index build can take minutes)..."
ok=0
for i in $(seq 1 60); do
  sleep 15
  if curl -sf -m 10 "http://127.0.0.1:$next/api/v1/healthz" | grep -q ok; then ok=1; break; fi
  # Bail out early if the container died.
  state=$(docker inspect "papervault-$next" --format "{{.State.Status}}" 2>/dev/null || echo gone)
  if [ "$state" != "running" ]; then echo "container exited early"; docker logs --tail 30 "papervault-$next"; exit 1; fi
done
if [ "$ok" != "1" ]; then echo "health check timed out"; docker logs --tail 30 "papervault-$next"; exit 1; fi
echo "papervault-$next healthy"

sed -i "s|proxy_pass http://127.0.0.1:$current;|proxy_pass http://127.0.0.1:$next;|" "$NGINX_CONF"
nginx -t && systemctl reload nginx
echo "nginx now upstream -> $next"

docker rm -f "papervault-$current" 2>/dev/null || true
docker rm -f papervault 2>/dev/null || true
docker compose down 2>/dev/null || true
docker image prune -f >/dev/null
echo "deploy complete"
