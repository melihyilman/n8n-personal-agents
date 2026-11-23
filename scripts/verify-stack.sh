#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY_BIN="python3"
if [ -x "$ROOT_DIR/host-bridge/.venv/bin/python3" ]; then
  PY_BIN="$ROOT_DIR/host-bridge/.venv/bin/python3"
fi

echo "============================================================================"
echo " Verifying n8n Personal AI Agent Stack & Running Automated Tests"
echo "============================================================================"

# 1. Validate all 4 n8n workflow JSON files
echo "[1/3] Validating n8n AI Agent workflow JSON schemas..."
"$PY_BIN" - <<'PY'
import json
from pathlib import Path

wf_dir = Path("workflows")
workflows = sorted(wf_dir.glob("*.json"))
assert len(workflows) == 4, f"Expected 4 workflows, found {len(workflows)}"
for wf in workflows:
    data = json.loads(wf.read_text(encoding="utf-8"))
    assert "name" in data and "nodes" in data and "connections" in data, f"Invalid schema in {wf}"
    agent_nodes = [n for n in data["nodes"] if "langchain" in n.get("type", "")]
    print(f"  ✓ {wf.name}: {len(data['nodes'])} nodes ({len(agent_nodes)} LangChain/AI Agent nodes)")
PY

# 2. Validate permissions.yaml and docker-compose.yml
echo "[2/3] Validating config/permissions.yaml & db/init.sql..."
"$PY_BIN" - <<'PY'
import yaml
from pathlib import Path

policy = yaml.safe_load(Path("config/permissions.yaml").read_text(encoding="utf-8"))
assert "workspace_security" in policy and "shell_permissions" in policy
sql = Path("db/init.sql").read_text(encoding="utf-8")
for table in ("dev_tasks", "dev_subtasks", "permission_approvals", "agent_error_lessons", "agent_skills_rag", "knowledge_chunks_rag"):
    assert table in sql, f"Missing table {table} in db/init.sql"
print("  ✓ permissions.yaml & db/init.sql verified")
PY

# 3. Run pytest on Host PC Bridge
echo "[3/3] Running pytest suite on host-bridge..."
if [ -x "$ROOT_DIR/host-bridge/.venv/bin/pytest" ]; then
  PYTHONPATH="$ROOT_DIR/host-bridge" "$ROOT_DIR/host-bridge/.venv/bin/pytest" "$ROOT_DIR/host-bridge/tests" -v
else
  PYTHONPATH="$ROOT_DIR/host-bridge" pytest "$ROOT_DIR/host-bridge/tests" -v
fi

echo "============================================================================"
echo " All verification checks passed!"
echo "============================================================================"

