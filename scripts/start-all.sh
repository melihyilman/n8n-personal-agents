#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p "$ROOT_DIR/.logs" "$ROOT_DIR/.n8n-home"

# Load .env if present
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

export PATH="/opt/homebrew/opt/node@24/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
N8N_BIN="${N8N_RUNTIME_BIN:-$(command -v n8n || true)}"
if [ -z "$N8N_BIN" ] && [ -x "$ROOT_DIR/../n8n/n8n-runtime/node_modules/.bin/n8n" ]; then
  N8N_BIN="$ROOT_DIR/../n8n/n8n-runtime/node_modules/.bin/n8n"
fi

launch_detached() {
  local log_file="$1"
  shift
  perl -MPOSIX -e 'POSIX::setsid() >= 0 or warn $!; open STDIN, "<", "/dev/null"; exec @ARGV or die "exec failed: $!"' -- "$@" > "$log_file" 2>&1 &
  disown 2>/dev/null || true
}

echo "============================================================================"
echo " Starting Full n8n Personal AI Agent Stack & Developer Mission Control Board"
echo "============================================================================"

# 1. Ensure .env exists
if [ ! -f "$ROOT_DIR/.env" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

# 2. Ensure Python virtualenv & packages for Host PC Bridge
if [ ! -d "$ROOT_DIR/host-bridge/.venv" ]; then
  echo "[1/5] Creating Python virtualenv for Host PC Bridge..."
  python3 -m venv "$ROOT_DIR/host-bridge/.venv"
  "$ROOT_DIR/host-bridge/.venv/bin/pip" install -q -r "$ROOT_DIR/host-bridge/requirements.txt"
fi

# 3. Start Host PC Bridge + Developer Dashboard on Port 8090 AND Port 3030
echo "[2/5] Starting Host PC Bridge & Mission Control Dashboard on :8090 and :3030..."
for port in 8090 3030; do
  existing_pid="$(lsof -tiTCP:$port -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$existing_pid" ]; then
    kill -9 $existing_pid 2>/dev/null || true
    sleep 0.5
  fi
  PYTHONPATH="$ROOT_DIR/host-bridge" \
    launch_detached "$ROOT_DIR/.logs/host-bridge-$port.log" \
    "$ROOT_DIR/host-bridge/.venv/bin/uvicorn" main:app --host 0.0.0.0 --port "$port"
done

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8090/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:3030/health >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
echo "  ✓ Developer Mission Control Board UI ready at: http://localhost:8090 and http://localhost:3030"
echo "  ✓ Interactive Swagger Tool Docs ready at:      http://localhost:8090/docs"

# 4. Start Local Ollama Daemon (:11434)
echo "[3/5] Checking Local Ollama LLM Daemon (:11434)..."
if command -v ollama >/dev/null 2>&1; then
  if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_ORIGINS="http://localhost:5678,http://127.0.0.1:5678,http://localhost:8090,http://localhost:3030" \
      launch_detached "$ROOT_DIR/.logs/ollama.log" ollama serve
    sleep 2
  fi
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "  ✓ Ollama daemon is ACTIVE at http://localhost:11434"
  fi
fi

# 5. Start Native n8n (:5678) and Import All 4 AI Agent Workflows
echo "[4/5] Starting Native n8n Engine on http://localhost:5678 & Importing 4 AI Agent Workflows..."
N8N_USER_DIR="${N8N_USER_FOLDER:-$ROOT_DIR/.n8n-home}"
if [ ! -d "$N8N_USER_DIR" ] && [ -d "$ROOT_DIR/../n8n/data/n8n-home" ]; then
  N8N_USER_DIR="$ROOT_DIR/../n8n/data/n8n-home"
fi

if [ -x "$N8N_BIN" ]; then
  # Import the 4 workflows if not already imported
  if [ ! -f "$ROOT_DIR/.logs/.workflows_imported" ]; then
    echo "  -> Importing 4 AI Agent workflows into n8n..."
    for wf in "$ROOT_DIR"/workflows/*.json; do
      N8N_USER_FOLDER="$N8N_USER_DIR" "$NODE_BIN" "$N8N_BIN" import:workflow --input="$wf" >> "$ROOT_DIR/.logs/n8n-import.log" 2>&1 || true
    done
    touch "$ROOT_DIR/.logs/.workflows_imported"
  fi

  if ! curl -fsS http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
    N8N_USER_FOLDER="$N8N_USER_DIR" \
      N8N_HOST=127.0.0.1 \
      N8N_PORT=5678 \
      N8N_PROTOCOL=http \
      WEBHOOK_URL="http://127.0.0.1:5678/" \
      HOST_BRIDGE_URL="http://127.0.0.1:8090" \
      OLLAMA_HOST="http://127.0.0.1:11434" \
      N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE=true \
      N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
      N8N_SECURE_COOKIE=false \
      N8N_DIAGNOSTICS_ENABLED=false \
      GENERIC_TIMEZONE="Europe/Istanbul" \
      TZ="Europe/Istanbul" \
      launch_detached "$ROOT_DIR/.logs/n8n.log" "$NODE_BIN" --max-old-space-size=1024 "$N8N_BIN" start

    echo "  -> Waiting for n8n (:5678) to finish booting..."
    for _ in $(seq 1 45); do
      if curl -fsS http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi

  if curl -fsS http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
    echo "  ✓ n8n Visual Workflow & Agents Canvas is ACTIVE at: http://localhost:5678"
  else
    echo "  ! n8n is still initializing; check $ROOT_DIR/.logs/n8n.log"
  fi
else
  echo "  ! Native n8n binary not found at $N8N_BIN"
fi

# 6. Run Live End-to-End Smoke Verification on All Services (:5678, :8090, :3030, :11434)
echo "[5/5] Running Live End-to-End Smoke Verification..."
"$ROOT_DIR/host-bridge/.venv/bin/python3" - <<'PY'
import httpx

for base in ("http://127.0.0.1:8090", "http://127.0.0.1:3030"):
    r_html = httpx.get(f"{base}/", timeout=5.0)
    assert r_html.status_code == 200 and "Developer Mission Control Board" in r_html.text
    r_health = httpx.get(f"{base}/health", timeout=5.0).json()
    assert r_health["status"] == "ok"

r_n8n = httpx.get("http://127.0.0.1:5678/healthz", timeout=5.0)
assert r_n8n.status_code == 200, f"n8n healthz returned {r_n8n.status_code}"

r_recall = httpx.post("http://127.0.0.1:8090/tools/rag/recall", json={
    "task_prompt": "Webhook payload $json.body and web crawler wait_for_js",
    "top_k_lessons": 3
}, timeout=10.0).json()
assert len(r_recall["error_lessons"]) >= 2

print("  ✓ n8n Workflow & Agents UI (http://localhost:5678) -> HTTP 200 OK!")
print("  ✓ Developer Mission Control Board (http://localhost:8090 & http://localhost:3030) -> HTTP 200 OK!")
print("  ✓ Host PC Bridge & Self-Learning RAG Recall -> HTTP 200 OK!")
PY

echo ""
echo "============================================================================"
echo " ALL SYSTEMS RUNNING! Open these URLs in your browser:"
echo "  👉 n8n Workflows & Agents UI (Port 5678):       http://localhost:5678"
echo "  👉 Developer Mission Control Board (Port 8090): http://localhost:8090"
echo "  👉 Developer Mission Control Board (Port 3030): http://localhost:3030"
echo "  👉 Host Bridge Interactive Swagger API:         http://localhost:8090/docs"
echo "============================================================================"
