#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "============================================================================"
echo " Bootstrapping n8n Personal AI Agent Orchestrator & Developer Board"
echo "============================================================================"

# 1. Create .env from .env.example if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[+] Created .env from .env.example"
fi

# 2. Ensure Ollama is running and pull local worker + embedding models
if command -v ollama >/dev/null 2>&1; then
  if ! pgrep -x "ollama" >/dev/null 2>&1; then
    echo "[+] Starting local Ollama daemon..."
    nohup ollama serve >/tmp/ollama-agent.log 2>&1 &
    sleep 3
  fi
  echo "[+] Ensuring local Ollama models (qwen2.5-coder:7b & nomic-embed-text) are available..."
  ollama pull nomic-embed-text || true
  ollama pull qwen2.5-coder:7b || true
else
  echo "[!] Ollama CLI not found in PATH. Install via: brew install ollama"
fi

# 3. Start Colima / Docker if needed and launch docker-compose stack
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    if command -v colima >/dev/null 2>&1; then
      echo "[+] Starting Colima Docker runtime..."
      colima start --cpu 4 --memory 8
    else
      echo "[!] Docker daemon is not running. Start Docker Desktop or Colima and re-run."
    fi
  fi

  if docker info >/dev/null 2>&1; then
    echo "[+] Starting PostgreSQL+pgvector, SearXNG, Crawl4AI, and n8n containers..."
    docker compose up -d
    echo "[+] Importing 4 n8n AI Agent Workflows into n8n container..."
    sleep 5
    for wf in /home/node/workflows/*.json; do
      docker exec -u node n8n-orchestrator n8n import:workflow --input="$wf" 2>/dev/null || true
    done
  fi
fi

# 4. Install Python virtualenv for macOS Host PC Bridge
echo "[+] Setting up macOS Host PC Bridge (host-bridge)..."
if [ ! -d "$ROOT_DIR/host-bridge/.venv" ]; then
  python3 -m venv "$ROOT_DIR/host-bridge/.venv"
fi
"$ROOT_DIR/host-bridge/.venv/bin/pip" install -q -r "$ROOT_DIR/host-bridge/requirements.txt"

echo ""
echo "============================================================================"
echo " Setup Complete!"
echo " - Start Host PC Bridge:   cd host-bridge && ./.venv/bin/uvicorn main:app --port 8090 --reload"
echo " - Start Developer Board:  cd dev-board && npm install && npm run dev"
echo " - Open Developer Board:   http://localhost:3030"
echo " - Open n8n Agents UI:     http://localhost:5678"
echo "============================================================================"

