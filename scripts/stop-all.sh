#!/usr/bin/env bash
set -euo pipefail

echo "Stopping Host PC Bridge & Developer Board on :8090 and :3030..."
for port in 8090 3030; do
  pid="$(lsof -ti tcp:$port 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    kill -9 $pid 2>/dev/null || true
    echo "  ✓ Stopped process on port $port"
  fi
done

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose down
fi
echo "All services stopped."

