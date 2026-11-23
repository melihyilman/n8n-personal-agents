"""
Automated Test Suite for the macOS Host PC & Capability Bridge
==============================================================
Verifies:
1. Local File & Codebase Intelligence Suite (tree, grep, read, write, surgical patch, semantic search)
2. 3-Tier Host PC Permission Engine (AUTO_SAFE, WORKSPACE_WRITE, APPROVAL_REQUIRED, BLOCKED)
3. Self-Learning Episodic Error RAG Loop (learn-error -> recall)
4. Local Skills Ingestion & Developer Board Task Creation with Pre-Task RAG Recall
"""

from pathlib import Path
from fastapi.testclient import TestClient

from main import app, BASE_DIR

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "n8n-personal-agent-host-bridge"


def test_local_file_suite_lifecycle(tmp_path: Path):
    # Create a temporary test directory inside the allowed workspace
    scratch_dir = BASE_DIR / "host-bridge" / ".pytest_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    sample_file = scratch_dir / "calculator.py"

    try:
        # 1. Write file
        write_resp = client.post(
            "/tools/fs/write",
            json={
                "file_path": str(sample_file),
                "content": "def add(a: int, b: int) -> int:\n    return a - b  # intentional bug\n",
                "overwrite": True,
            },
        )
        assert write_resp.status_code == 200
        assert write_resp.json()["status"] == "written"

        # 2. Read file with line numbers
        read_resp = client.post(
            "/tools/fs/read",
            json={"file_path": str(sample_file), "start_line": 1, "end_line": 10},
        )
        assert read_resp.status_code == 200
        assert "return a - b" in read_resp.json()["raw_content"]

        # 3. Grep for pattern across workspace
        grep_resp = client.post(
            "/tools/fs/grep",
            json={
                "workspace_path": str(scratch_dir),
                "pattern": "intentional bug",
            },
        )
        assert grep_resp.status_code == 200
        assert grep_resp.json()["count"] >= 1

        # 4. Apply surgical patch
        patch_resp = client.post(
            "/tools/fs/patch",
            json={
                "file_path": str(sample_file),
                "target": "    return a - b  # intentional bug",
                "replacement": "    return a + b  # fixed by local worker",
            },
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["replacements_made"] == 1

        # 5. Verify patched content
        verify_read = client.post(
            "/tools/fs/read",
            json={"file_path": str(sample_file), "start_line": 1, "end_line": 10},
        )
        assert "return a + b  # fixed by local worker" in verify_read.json()["raw_content"]

        # 6. Directory tree check
        tree_resp = client.post(
            "/tools/fs/tree",
            json={"workspace_path": str(scratch_dir), "max_depth": 2},
        )
        assert tree_resp.status_code == 200
        assert any(e["path"] == "calculator.py" for e in tree_resp.json()["tree"])
    finally:
        if sample_file.exists():
            sample_file.unlink()
        if scratch_dir.exists():
            scratch_dir.rmdir()


def test_permission_tiers_and_approval_gate():
    # Tier 1: AUTO_SAFE command executes immediately
    safe_resp = client.post(
        "/tools/shell/exec",
        json={"command": "pwd", "workspace_path": str(BASE_DIR)},
    )
    assert safe_resp.status_code == 200
    assert safe_resp.json()["status"] == "executed"
    assert safe_resp.json()["permission_tier"] == "AUTO_SAFE"

    # Tier 3: APPROVAL_REQUIRED command pauses and creates an approval gate record
    risky_resp = client.post(
        "/tools/shell/exec",
        json={"command": "rm -rf ./some_temp_build_dir", "workspace_path": str(BASE_DIR)},
    )
    assert risky_resp.status_code == 200
    risky_data = risky_resp.json()
    assert risky_data["status"] == "awaiting_approval"
    assert risky_data["permission_tier"] == "APPROVAL_REQUIRED"
    assert "approval_id" in risky_data

    # Blocked command returns HTTP 403
    blocked_resp = client.post(
        "/tools/shell/exec",
        json={"command": "sudo rm -rf /", "workspace_path": str(BASE_DIR)},
    )
    assert blocked_resp.status_code == 403

    # Restricted path access returns HTTP 403
    ssh_resp = client.post(
        "/tools/fs/read",
        json={"file_path": "/etc/passwd"},
    )
    assert ssh_resp.status_code == 403


def test_self_learning_error_rag_loop():
    # 1. Teach a new error lesson
    learn_resp = client.post(
        "/tools/rag/learn-error",
        json={
            "domain": "web_crawler",
            "failure_signature": "Cloudflare 403 on raw curl scrape",
            "failed_action": "curl https://docs.example.com/api",
            "error_trace": "HTTP 403 Forbidden - Enable JavaScript and cookies to continue",
            "root_cause": "Raw curl lacks headless browser JS execution.",
            "learned_rule": "RULE: For JS-protected documentation sites, always use /tools/web/crawl-page with wait_for_js=true.",
            "verified_fix": "POST /tools/web/crawl-page {\"url\": \"https://docs.example.com/api\", \"wait_for_js\": true}",
        },
    )
    assert learn_resp.status_code == 200
    assert learn_resp.json()["status"] == "learned"

    # 2. Recall the lesson on a future web crawling prompt
    recall_resp = client.post(
        "/tools/rag/recall",
        json={
            "task_prompt": "Crawl https://docs.example.com/api documentation that requires JavaScript",
            "domain": "web_crawler",
        },
    )
    assert recall_resp.status_code == 200
    recall_data = recall_resp.json()
    assert len(recall_data["error_lessons"]) >= 1
    assert "wait_for_js=true" in recall_data["compiled_guardrails"]


def test_developer_board_task_creation_and_state():
    create_resp = client.post(
        "/api/board/tasks",
        json={
            "title": "Implement Local File Semantic Search & Crawl Docs",
            "prompt": "Search the web for pgvector ivfflat index tuning, crawl the docs, and update db/init.sql.",
            "orchestrator_provider": "claude",
            "orchestrator_model": "claude-sonnet-4-20250514",
            "worker_model": "qwen2.5-coder:7b",
            "auto_dispatch_to_n8n": False,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["task"]["orchestrator_provider"] == "claude"
    assert len(created["subtasks"]) == 3
    assert len(created["task"]["rag_context_injected"]) >= 1

    board_resp = client.get("/api/board/state")
    assert board_resp.status_code == 200
    assert len(board_resp.json()["tasks"]) >= 2

