"""
n8n Personal AI Agent - macOS Host PC & Capability Bridge
=========================================================
Provides n8n AI Agents and the Developer Mission Control Board with:
1. Local File & Codebase Intelligence Suite (/tools/fs/*)
   - tree, grep (ripgrep), read (line-range), write, surgical patch, semantic file search
2. Web Search & Deep Web Crawler Suite (/tools/web/*)
   - search (SearXNG + DuckDuckGo HTML fallback), crawl-page (Crawl4AI + local HTTP/HTML->MD fallback),
     deep-crawl (recursive multi-page documentation crawler with auto-RAG indexing)
3. Host PC Terminal & Process Suite (/tools/shell/*)
   - 3-Tier Permission Policy (AUTO_SAFE, WORKSPACE_WRITE, APPROVAL_REQUIRED, BLOCKED)
4. Database Inspection & Query Suite (/tools/db/*)
   - schema inspection, parameterized SQL query execution with permission gates
5. Self-Learning Episodic & Procedural RAG Engine (/tools/rag/*)
   - error post-mortem lesson storage, semantic recall via Ollama nomic-embed-text + pgvector,
     and local skill ingestion ($SKILLS_DIR)
6. Developer Mission Control Board API (/api/board/*)
   - Tasks, subtasks, live permission approvals, RAG brain inspector, and n8n webhook trigger
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.parse
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import html2text
import httpx
import yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False

BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.path.expanduser(os.path.expandvars(os.getenv("WORKSPACE_ROOT", str(BASE_DIR))))).resolve()
SKILLS_DIR = os.path.expanduser(os.path.expandvars(os.getenv("SKILLS_DIR", "~/.gemini/config/skills")))
PERMISSIONS_PATH = BASE_DIR / "config" / "permissions.yaml"
LOCAL_STATE_FILE = BASE_DIR / "host-bridge" / ".local_state.json"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://n8n_agent:n8n_agent_secret@localhost:5432/n8n_orchestrator",
)
OLLAMA_URL = os.getenv("OLLAMA_LOCAL_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_WORKER_MODEL = os.getenv("OLLAMA_WORKER_MODEL", "qwen2.5-coder:7b")
SEARXNG_URL = os.getenv("SEARXNG_LOCAL_URL", "http://localhost:8088")
CRAWL4AI_URL = os.getenv("CRAWL4AI_LOCAL_URL", "http://localhost:11235")
N8N_WEBHOOK_BASE = os.getenv("N8N_WEBHOOK_BASE", "http://localhost:5678")

app = FastAPI(
    title="n8n Personal Agent - Host PC & Capability Bridge",
    version="1.0.0",
    description="Local File Intelligence, Web Search & Deep Crawler, PC Terminal, DB, and Self-Learning RAG API for n8n Agents.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Configuration & Permission Policy Engine
# ============================================================================

def load_permissions() -> Dict[str, Any]:
    if PERMISSIONS_PATH.exists():
        with open(PERMISSIONS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def resolve_and_validate_path(raw_path: str, require_write: bool = False) -> Path:
    policy = load_permissions().get("workspace_security", {})
    os.environ.setdefault("WORKSPACE_ROOT", str(WORKSPACE_ROOT))

    def _expand_paths(raw_list: List[str]) -> List[Path]:
        resolved: List[Path] = []
        for p in raw_list:
            expanded = os.path.expanduser(os.path.expandvars(p)).strip()
            if expanded:
                resolved.append(Path(expanded).resolve())
        return resolved

    allowed_roots = _expand_paths(policy.get("allowed_workspaces", [])) + [WORKSPACE_ROOT, BASE_DIR]
    writable_roots = _expand_paths(policy.get("writable_workspaces", [])) + [WORKSPACE_ROOT, BASE_DIR]
    blocked_roots = _expand_paths(policy.get("blocked_paths", ["/System", "/etc"]))

    raw_expanded = os.path.expanduser(os.path.expandvars(raw_path))
    target = (WORKSPACE_ROOT / raw_expanded).resolve() if not Path(raw_expanded).is_absolute() else Path(raw_expanded).resolve()

    for blocked in blocked_roots:
        if target == blocked or blocked in target.parents:
            raise HTTPException(
                status_code=403,
                detail=f"Access BLOCKED by policy: '{target}' is inside restricted path '{blocked}'.",
            )

    roots_to_check = writable_roots if require_write else allowed_roots
    if not any(target == root or root in target.parents for root in roots_to_check):
        mode_label = "write" if require_write else "read"
        raise HTTPException(
            status_code=403,
            detail=f"Path '{target}' is outside allowed {mode_label} workspaces: {[str(r) for r in roots_to_check]}",
        )
    return target


def classify_shell_command(command: str) -> Tuple[str, str]:
    """
    Returns (tier, reason) where tier is one of:
    AUTO_SAFE | WORKSPACE_WRITE | APPROVAL_REQUIRED | BLOCKED
    """
    cmd_stripped = command.strip()
    policy = load_permissions().get("shell_permissions", {})

    for blocked in policy.get("blocked_patterns", []):
        if blocked.lower() in cmd_stripped.lower():
            return ("BLOCKED", f"Matched blocked pattern: '{blocked}'")

    for approval_pat in policy.get("approval_required_patterns", []):
        if approval_pat.lower() in cmd_stripped.lower():
            return ("APPROVAL_REQUIRED", f"Matched high-risk pattern requiring approval: '{approval_pat}'")

    # Check chaining operators that could hide destructive commands
    segments = [seg.strip() for seg in re.split(r"[;&|]+", cmd_stripped) if seg.strip()]
    auto_safe = policy.get("auto_safe_prefixes", [])
    ws_write = policy.get("workspace_write_prefixes", [])

    all_safe = True
    all_ws_or_safe = True
    for seg in segments:
        is_seg_safe = any(seg == p.strip() or seg.startswith(p.strip() + " ") or seg.startswith(p) for p in auto_safe)
        is_seg_ws = any(seg == p.strip() or seg.startswith(p.strip() + " ") or seg.startswith(p) for p in ws_write)
        if not is_seg_safe:
            all_safe = False
        if not (is_seg_safe or is_seg_ws):
            all_ws_or_safe = False

    if all_safe and segments:
        return ("AUTO_SAFE", "All command segments match auto_safe_prefixes")
    if all_ws_or_safe and segments:
        return ("WORKSPACE_WRITE", "Command matches workspace_write_prefixes")
    return ("APPROVAL_REQUIRED", "Unlisted command prefix requires Developer Board approval")


def classify_sql_statement(sql: str) -> Tuple[str, str]:
    cleaned = sql.strip().upper()
    first_token = cleaned.split()[0] if cleaned else ""
    if first_token in ("SELECT", "EXPLAIN", "SHOW", "WITH"):
        if any(kw in cleaned for kw in ("DELETE ", "DROP ", "TRUNCATE ", "UPDATE ", "INSERT ")):
            return ("APPROVAL_REQUIRED", "CTE or query contains mutating SQL keywords")
        return ("AUTO_SAFE", f"Read-only SQL statement ({first_token})")
    if first_token in ("INSERT", "UPDATE", "CREATE", "ALTER"):
        return ("WORKSPACE_WRITE", f"Workspace write SQL statement ({first_token})")
    return ("APPROVAL_REQUIRED", f"Destructive or privileged SQL statement ({first_token})")


# ============================================================================
# Persistent Storage Helper (PostgreSQL + Local JSON Fallback for Offline Dev)
# ============================================================================

def _default_local_state() -> Dict[str, Any]:
    return {
        "dev_tasks": [
            {
                "id": "task-demo-001",
                "title": "Build Self-Healing Web Crawler & Local Code Patcher Flow",
                "prompt": "Crawl the latest n8n AI Agent docs, extract tool-calling patterns, verify local host-bridge endpoints, and run pytest.",
                "workspace_path": str(BASE_DIR),
                "status": "completed",
                "orchestrator_provider": "gemini",
                "orchestrator_model": "gemini-2.5-pro",
                "worker_model": "qwen2.5-coder:7b",
                "permissions_profile": {"web_crawl": True, "fs_write": True, "db_write": True, "shell_exec": True},
                "rag_context_injected": [
                    "RULE: When scraping modern documentation, use /tools/web/crawl-page with wait_for_js=true.",
                    "RULE: Always call /tools/fs/read on the target line range before calling /tools/fs/patch."
                ],
                "execution_plan": [
                    {"step": 1, "specialist": "web_researcher", "title": "Search & crawl n8n LangChain Agent docs", "status": "completed"},
                    {"step": 2, "specialist": "local_coder", "title": "Patch local workflow tool definitions", "status": "completed"},
                    {"step": 3, "specialist": "pc_shell_runner", "title": "Run pytest suite via Host Bridge", "status": "completed"}
                ],
                "final_output": "All 3 subtasks completed via Local Ollama worker (qwen2.5-coder:7b). 0 errors, 2 RAG rules applied.",
                "token_metrics": {"paid_input_tokens": 1420, "paid_output_tokens": 380, "local_tokens": 9850, "estimated_saved_usd": 0.29},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "dev_subtasks": [
            {
                "id": "sub-demo-001",
                "task_id": "task-demo-001",
                "step_order": 1,
                "title": "Search & crawl n8n LangChain Agent docs",
                "assigned_specialist": "web_researcher",
                "model_tier": "local",
                "prompt_payload": "Use web_search and web_crawl_page to extract n8n AI Agent tool parameters.",
                "status": "completed",
                "tool_calls": [{"tool": "web_search", "query": "n8n AI Agent toolWorkflow node"}, {"tool": "web_crawl_page", "url": "https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/"}],
                "result_output": "Extracted 14 KB clean Markdown and indexed 6 chunks into pgvector.",
                "retry_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "sub-demo-002",
                "task_id": "task-demo-001",
                "step_order": 2,
                "title": "Verify & patch local files with qwen2.5-coder:7b",
                "assigned_specialist": "local_coder",
                "model_tier": "local",
                "prompt_payload": "Inspect host-bridge endpoints using fs_grep and fs_read, then run pytest.",
                "status": "completed",
                "tool_calls": [{"tool": "fs_tree", "path": str(BASE_DIR)}, {"tool": "shell_exec", "command": "pytest"}],
                "result_output": "All local unit tests passed.",
                "retry_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "permission_approvals": [],
        "agent_error_lessons": [
            {
                "id": "lesson-001",
                "domain": "sql_query",
                "failure_signature": "column does not exist in dev_tasks",
                "failed_action": "SELECT task_name FROM dev_tasks",
                "error_trace": "UndefinedColumn: column task_name does not exist",
                "root_cause": "Agent guessed column name task_name instead of inspecting the schema first.",
                "learned_rule": "RULE: Before writing any SQL query against a table, ALWAYS call /tools/db/schema to verify exact column names.",
                "verified_fix": "SELECT id, title, status FROM dev_tasks",
                "hit_count": 4,
                "confidence_score": 0.98,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "lesson-002",
                "domain": "web_crawler",
                "failure_signature": "Empty content from SPA documentation page",
                "failed_action": "Raw HTTP GET on client-rendered docs page",
                "error_trace": "Extracted text length < 50 chars",
                "root_cause": "Target website uses client-side JS rendering which raw HTTP GET cannot parse.",
                "learned_rule": "RULE: When scraping modern documentation or JS-rendered websites, ALWAYS use /tools/web/crawl-page with wait_for_js=true.",
                "verified_fix": "POST /tools/web/crawl-page {\"url\": \"...\", \"wait_for_js\": true}",
                "hit_count": 7,
                "confidence_score": 0.99,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "lesson-003",
                "domain": "local_fs",
                "failure_signature": "Patch target mismatch due to stale line contents",
                "failed_action": "POST /tools/fs/patch with guessed indentation",
                "error_trace": "ValueError: Target block not found in file",
                "root_cause": "Agent attempted to patch a file without reading the current lines first.",
                "learned_rule": "RULE: Always call /tools/fs/read on the target line range immediately before calling /tools/fs/patch.",
                "verified_fix": "Call /tools/fs/read first, then copy exact target block into /tools/fs/patch",
                "hit_count": 5,
                "confidence_score": 0.96,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "agent_skills_rag": [
            {
                "id": "skill-001",
                "skill_name": "hybrid-code-implementer",
                "category": "engineering",
                "description": "Local LLM specialist for inspecting local codebases, applying surgical patches, and running unit tests.",
                "system_instructions": "Always run fs_tree or fs_grep before editing files. Use fs_read with line ranges to inspect exact context, apply changes via fs_patch, and immediately verify using shell_exec with pytest/npm test.",
                "allowed_tools": ["fs_tree", "fs_grep", "fs_read", "fs_patch", "fs_write", "shell_exec"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "skill-002",
                "skill_name": "deep-web-researcher",
                "category": "research",
                "description": "Autonomous web search and headless browser crawler that extracts clean Markdown and indexes docs into pgvector.",
                "system_instructions": "Start with web_search (SearXNG) to discover official documentation URLs. Then call web_crawl_page for single pages or web_deep_crawl to ingest multi-page documentation into knowledge_chunks_rag.",
                "allowed_tools": ["web_search", "web_crawl_page", "web_deep_crawl", "rag_query"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "skill-003",
                "skill_name": "database-architect-operator",
                "category": "database",
                "description": "Database schema inspector and safe parameterized SQL executor.",
                "system_instructions": "Never guess column names. Always call db_schema first to inspect tables, columns, and types before running db_query.",
                "allowed_tools": ["db_schema", "db_query", "rag_query"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "knowledge_chunks_rag": [],
    }


def load_local_state() -> Dict[str, Any]:
    if LOCAL_STATE_FILE.exists():
        try:
            with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    state = _default_local_state()
    save_local_state(state)
    return state


def save_local_state(state: Dict[str, Any]) -> None:
    LOCAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def pg_available() -> bool:
    if not HAS_PSYCOPG:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=1) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception:
        return False


# ============================================================================
# Embeddings & Vector Similarity (Ollama nomic-embed-text + Deterministic Fallback)
# ============================================================================

async def compute_embedding(text: str) -> List[float]:
    """
    Calls local Ollama (`nomic-embed-text`, 768-dim). If Ollama daemon is offline,
    computes a deterministic 768-dim L2-normalized token-hash embedding so RAG
    recall works 100% offline and in unit tests.
    """
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": text[:6000]},
            )
            if resp.status_code == 200:
                vec = resp.json().get("embedding")
                if isinstance(vec, list) and len(vec) == 768:
                    return vec
    except Exception:
        pass

    # Deterministic 768-d hash projection fallback
    vec = [0.0] * 768
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % 768
        sign = 1.0 if ((h >> 10) & 1) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def lexical_overlap_score(query: str, text: str) -> float:
    q_tokens = set(re.findall(r"[a-zA-Z0-9_]{2,}", query.lower()))
    t_tokens = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
    if not q_tokens or not t_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / len(q_tokens)


# ============================================================================
# 1. Local File & Codebase Intelligence Suite (/tools/fs/*)
# ============================================================================

class FsTreeRequest(BaseModel):
    workspace_path: str = Field(default=str(BASE_DIR))
    max_depth: int = Field(default=3, ge=1, le=8)
    include_hidden: bool = Field(default=False)


class FsGrepRequest(BaseModel):
    workspace_path: str = Field(default=str(BASE_DIR))
    pattern: str = Field(..., description="Regex or literal pattern to search for")
    file_glob: Optional[str] = Field(default=None, description="Optional glob e.g. '*.py' or '*.ts'")
    case_sensitive: bool = Field(default=False)
    max_matches: int = Field(default=50, ge=1, le=200)


class FsReadRequest(BaseModel):
    file_path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=250, ge=1)


class FsWriteRequest(BaseModel):
    file_path: str
    content: str
    overwrite: bool = Field(default=True)


class FsPatchRequest(BaseModel):
    file_path: str
    target: str = Field(..., description="Exact character sequence to find in the file")
    replacement: str = Field(..., description="Replacement content")


class FsSemanticSearchRequest(BaseModel):
    workspace_path: str = Field(default=str(BASE_DIR))
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    reindex: bool = Field(default=False)


IGNORED_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}


@app.post("/tools/fs/tree")
def tool_fs_tree(req: FsTreeRequest) -> Dict[str, Any]:
    root = resolve_and_validate_path(req.workspace_path, require_write=False)
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {root}")

    entries: List[Dict[str, Any]] = []

    def _walk(curr: Path, depth: int) -> None:
        if depth > req.max_depth or len(entries) >= 500:
            return
        try:
            children = sorted(curr.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for child in children:
            if not req.include_hidden and child.name.startswith(".") and child.name not in (".env.example",):
                continue
            if child.is_dir() and child.name in IGNORED_DIRS:
                continue
            rel = str(child.relative_to(root))
            entries.append({
                "path": rel,
                "type": "directory" if child.is_dir() else "file",
                "size_bytes": child.stat().st_size if child.is_file() else None,
            })
            if child.is_dir():
                _walk(child, depth + 1)

    _walk(root, 1)
    return {
        "workspace_path": str(root),
        "total_entries": len(entries),
        "tree": entries,
    }


@app.post("/tools/fs/grep")
def tool_fs_grep(req: FsGrepRequest) -> Dict[str, Any]:
    root = resolve_and_validate_path(req.workspace_path, require_write=False)
    matches: List[Dict[str, Any]] = []

    # Try ripgrep first if installed, fallback to native Python regex walk
    rg_bin = "/opt/homebrew/bin/rg" if Path("/opt/homebrew/bin/rg").exists() else "rg"
    rg_cmd = [rg_bin, "--json", "--max-count", str(req.max_matches)]
    if not req.case_sensitive:
        rg_cmd.append("-i")
    if req.file_glob:
        rg_cmd.extend(["-g", req.file_glob])
    rg_cmd.extend([req.pattern, str(root)])

    try:
        proc = subprocess.run(rg_cmd, capture_output=True, text=True, timeout=10)
        for line in proc.stdout.splitlines():
            try:
                evt = json.loads(line)
                if evt.get("type") == "match":
                    data = evt["data"]
                    matches.append({
                        "file": data["path"]["text"],
                        "line_number": data["line_number"],
                        "line_text": data["lines"]["text"].rstrip("\n"),
                    })
                    if len(matches) >= req.max_matches:
                        break
            except Exception:
                continue
        return {"pattern": req.pattern, "engine": "ripgrep", "count": len(matches), "matches": matches}
    except FileNotFoundError:
        flags = 0 if req.case_sensitive else re.IGNORECASE
        regex = re.compile(req.pattern, flags)
        for path in root.rglob(req.file_glob or "*"):
            if any(part in IGNORED_DIRS for part in path.parts) or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for idx, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append({
                            "file": str(path),
                            "line_number": idx,
                            "line_text": line[:300],
                        })
                        if len(matches) >= req.max_matches:
                            return {"pattern": req.pattern, "engine": "python-re", "count": len(matches), "matches": matches}
            except Exception:
                continue
        return {"pattern": req.pattern, "engine": "python-re", "count": len(matches), "matches": matches}


@app.post("/tools/fs/read")
def tool_fs_read(req: FsReadRequest) -> Dict[str, Any]:
    target = resolve_and_validate_path(req.file_path, require_write=False)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {target}")

    text = target.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()
    total_lines = len(all_lines)
    start = max(1, req.start_line)
    end = min(total_lines, max(start, req.end_line))
    selected = all_lines[start - 1 : end]
    numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start))

    return {
        "file_path": str(target),
        "total_lines": total_lines,
        "start_line": start,
        "end_line": end,
        "raw_content": "\n".join(selected),
        "numbered_content": numbered,
    }


@app.post("/tools/fs/write")
def tool_fs_write(req: FsWriteRequest) -> Dict[str, Any]:
    target = resolve_and_validate_path(req.file_path, require_write=True)
    if target.exists() and not req.overwrite:
        raise HTTPException(status_code=409, detail=f"File already exists and overwrite=False: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {
        "status": "written",
        "file_path": str(target),
        "bytes_written": len(req.content.encode("utf-8")),
        "lines_written": len(req.content.splitlines()),
    }


@app.post("/tools/fs/patch")
def tool_fs_patch(req: FsPatchRequest) -> Dict[str, Any]:
    target = resolve_and_validate_path(req.file_path, require_write=True)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {target}")
    content = target.read_text(encoding="utf-8")
    occurrences = content.count(req.target)
    if occurrences == 0:
        raise HTTPException(
            status_code=422,
            detail="Target block not found in file. Rule: Always call /tools/fs/read first to verify exact lines and whitespace.",
        )
    updated = content.replace(req.target, req.replacement)
    target.write_text(updated, encoding="utf-8")
    return {
        "status": "patched",
        "file_path": str(target),
        "replacements_made": occurrences,
        "new_total_lines": len(updated.splitlines()),
    }


@app.post("/tools/fs/semantic-search")
async def tool_fs_semantic_search(req: FsSemanticSearchRequest) -> Dict[str, Any]:
    root = resolve_and_validate_path(req.workspace_path, require_write=False)
    query_vec = await compute_embedding(req.query)

    scored_chunks: List[Dict[str, Any]] = []
    allowed_exts = {".py", ".ts", ".tsx", ".js", ".json", ".md", ".sql", ".yaml", ".yml", ".sh"}

    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts) or not path.is_file():
            continue
        if path.suffix.lower() not in allowed_exts or path.stat().st_size > 200_000:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if not content.strip():
                continue
            snippet = content[:1500]
            lex_score = lexical_overlap_score(req.query, f"{path.name}\n{snippet}")
            emb = await compute_embedding(f"{path.name}\n{snippet}")
            sim = 0.65 * cosine_similarity(query_vec, emb) + 0.35 * lex_score
            scored_chunks.append({
                "file_path": str(path.relative_to(root)),
                "absolute_path": str(path),
                "score": round(sim, 4),
                "preview": "\n".join(content.splitlines()[:25]),
            })
        except Exception:
            continue

    scored_chunks.sort(key=lambda x: x["score"], reverse=True)
    return {
        "workspace_path": str(root),
        "query": req.query,
        "results": scored_chunks[: req.top_k],
    }


# ============================================================================
# 2. Web Search & Deep Web Crawler Suite (/tools/web/*)
# ============================================================================

class WebSearchRequest(BaseModel):
    query: str
    max_results: int = Field(default=6, ge=1, le=20)
    categories: str = Field(default="general,it")


class WebCrawlPageRequest(BaseModel):
    url: str
    wait_for_js: bool = Field(default=True)
    index_into_rag: bool = Field(default=True)
    max_chars: int = Field(default=25000, ge=500, le=100000)


class WebDeepCrawlRequest(BaseModel):
    seed_url: str
    max_depth: int = Field(default=2, ge=1, le=3)
    max_pages: int = Field(default=8, ge=1, le=35)
    same_domain_only: bool = Field(default=True)
    url_path_prefix: Optional[str] = Field(default=None)
    index_into_rag: bool = Field(default=True)


def _html_to_clean_markdown(html_raw: str, base_url: str) -> Tuple[str, str, List[str]]:
    soup = BeautifulSoup(html_raw, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else base_url)

    # Extract internal/outbound links before stripping nav
    links: List[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(base_url, a_tag["href"]).split("#")[0]
        if href.startswith(("http://", "https://")) and href not in links:
            links.append(href)

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "footer", "nav"]):
        tag.decompose()

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    md = converter.handle(str(soup)).strip()
    md = re.sub(r"\n{3,}", "\n\n", md)
    return title, md, links


@app.post("/tools/web/search")
async def tool_web_search(req: WebSearchRequest) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    engine_used = "searxng"

    # 1. Try self-hosted SearXNG JSON API first
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": req.query, "format": "json", "categories": req.categories},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", [])[: req.max_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                        "engine": item.get("engine", "searxng"),
                    })
    except Exception:
        pass

    # 2. Fallback to DuckDuckGo Lite HTML scraper if SearXNG container isn't running yet
    if not results:
        engine_used = "duckduckgo-fallback"
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers, verify=False) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": req.query},
                )
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for res in soup.select(".result")[: req.max_results]:
                        a_el = res.select_one(".result__a")
                        snip_el = res.select_one(".result__snippet")
                        if not a_el:
                            continue
                        raw_href = a_el.get("href", "")
                        parsed = urllib.parse.urlparse(raw_href)
                        qs = urllib.parse.parse_qs(parsed.query)
                        clean_url = qs.get("uddg", [raw_href])[0]
                        results.append({
                            "title": a_el.get_text(strip=True),
                            "url": clean_url,
                            "snippet": snip_el.get_text(strip=True) if snip_el else "",
                            "engine": "duckduckgo",
                        })
        except Exception:
            pass

    # 3. Offline / Sandbox Built-in Knowledge & Local Server Discovery Fallback
    if not results:
        engine_used = "local-knowledge-index"
        results = [
            {
                "title": "n8n Personal Agent Host Bridge OpenAPI Documentation",
                "url": "http://127.0.0.1:8090/openapi.json",
                "snippet": f"Live local capability endpoints matching query: {req.query}",
                "engine": "local-openapi",
            },
            {
                "title": "n8n AI Agent & LangChain Workflow Architecture Guide",
                "url": "https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/",
                "snippet": "Official n8n AI Agent node documentation for tools, memory, and hybrid LLM orchestration.",
                "engine": "curated-docs",
            },
        ][: req.max_results]

    return {
        "query": req.query,
        "engine": engine_used,
        "count": len(results),
        "results": results,
    }


async def _fetch_page_markdown(url: str, wait_for_js: bool = True) -> Dict[str, Any]:
    """
    Attempts Crawl4AI headless browser first; falls back to direct HTTP + BeautifulSoup/HTML2Text.
    """
    if "/tools/web/" in url:
        raise HTTPException(status_code=403, detail="Recursive crawling of /tools/web/ endpoints is blocked.")

    # 1. Try Crawl4AI container
    if wait_for_js:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"{CRAWL4AI_URL}/crawl",
                    json={"urls": [url], "word_count_threshold": 5},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    results = payload.get("results") or []
                    if results:
                        first = results[0]
                        md = first.get("markdown") or ""
                        html_raw = first.get("html") or ""
                        title, fallback_md, links = _html_to_clean_markdown(html_raw, url) if html_raw else (url, md, [])
                        return {
                            "url": url,
                            "title": first.get("metadata", {}).get("title") or title,
                            "markdown": md or fallback_md,
                            "links": links[:60],
                            "engine": "crawl4ai-playwright",
                        }
        except Exception:
            pass

    # 2. Direct HTTP client + HTML2Text extractor
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) n8nPersonalAgent/1.0"}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers, verify=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        title, md, links = _html_to_clean_markdown(resp.text, url)
        return {
            "url": url,
            "title": title,
            "markdown": md,
            "links": links[:60],
            "engine": "httpx-html2text",
        }


async def _index_markdown_chunks(source_type: str, source_uri: str, title: str, markdown_text: str) -> int:
    # Split Markdown into ~1200-char overlapping chunks
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", markdown_text) if p.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= 1200:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)
            current = para[:1200]
    if current:
        chunks.append(current)

    chunks = chunks[:25]  # Cap chunks per single page
    state = load_local_state()
    state["knowledge_chunks_rag"] = [
        c for c in state.get("knowledge_chunks_rag", []) if c.get("source_uri") != source_uri
    ]

    for idx, chunk_text in enumerate(chunks):
        emb = await compute_embedding(f"{title}\n{chunk_text}")
        chunk_record = {
            "id": str(uuid.uuid4()),
            "source_type": source_type,
            "source_uri": source_uri,
            "title": title,
            "chunk_index": idx,
            "content_markdown": chunk_text,
            "embedding": emb,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        state["knowledge_chunks_rag"].append(chunk_record)

        if pg_available():
            try:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        if idx == 0:
                            cur.execute("DELETE FROM knowledge_chunks_rag WHERE source_uri = %s", (source_uri,))
                        cur.execute(
                            """
                            INSERT INTO knowledge_chunks_rag
                            (source_type, source_uri, title, chunk_index, content_markdown, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s::vector)
                            """,
                            (source_type, source_uri, title, idx, chunk_text, json.dumps(emb)),
                        )
                    conn.commit()
            except Exception:
                pass

    save_local_state(state)
    return len(chunks)


@app.post("/tools/web/crawl-page")
async def tool_web_crawl_page(req: WebCrawlPageRequest) -> Dict[str, Any]:
    try:
        page = await _fetch_page_markdown(req.url, wait_for_js=req.wait_for_js)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to crawl '{req.url}': {exc}")

    md_trimmed = page["markdown"][: req.max_chars]
    chunks_indexed = 0
    if req.index_into_rag and md_trimmed:
        chunks_indexed = await _index_markdown_chunks("web_crawl", req.url, page["title"], md_trimmed)

    return {
        "url": req.url,
        "title": page["title"],
        "engine": page["engine"],
        "markdown_length": len(md_trimmed),
        "chunks_indexed_in_rag": chunks_indexed,
        "discovered_links_count": len(page["links"]),
        "discovered_links": page["links"][:20],
        "markdown": md_trimmed,
    }


@app.post("/tools/web/deep-crawl")
async def tool_web_deep_crawl(req: WebDeepCrawlRequest) -> Dict[str, Any]:
    seed_parsed = urllib.parse.urlparse(req.seed_url)
    seed_domain = seed_parsed.netloc

    queue: deque[Tuple[str, int]] = deque([(req.seed_url, 0)])
    visited: set[str] = set()
    crawled_pages: List[Dict[str, Any]] = []
    total_chunks = 0

    while queue and len(crawled_pages) < req.max_pages:
        curr_url, depth = queue.popleft()
        if curr_url in visited or depth > req.max_depth:
            continue
        visited.add(curr_url)

        try:
            page = await _fetch_page_markdown(curr_url, wait_for_js=True)
            md_trimmed = page["markdown"][:20000]
            c_count = 0
            if req.index_into_rag and md_trimmed:
                c_count = await _index_markdown_chunks("web_crawl", curr_url, page["title"], md_trimmed)
                total_chunks += c_count

            crawled_pages.append({
                "url": curr_url,
                "depth": depth,
                "title": page["title"],
                "markdown_chars": len(md_trimmed),
                "chunks_indexed": c_count,
            })

            if depth < req.max_depth:
                for link in page["links"]:
                    lp = urllib.parse.urlparse(link)
                    if req.same_domain_only and lp.netloc != seed_domain:
                        continue
                    if req.url_path_prefix and not lp.path.startswith(req.url_path_prefix):
                        continue
                    if link not in visited:
                        queue.append((link, depth + 1))
        except Exception as exc:
            crawled_pages.append({
                "url": curr_url,
                "depth": depth,
                "error": str(exc),
            })

    return {
        "seed_url": req.seed_url,
        "pages_crawled": len(crawled_pages),
        "total_rag_chunks_indexed": total_chunks,
        "pages": crawled_pages,
    }


# ============================================================================
# 3. Host PC Terminal & Process Suite (/tools/shell/*)
# ============================================================================

class ShellExecRequest(BaseModel):
    command: str
    workspace_path: str = Field(default=str(BASE_DIR))
    timeout_seconds: int = Field(default=45, ge=1, le=300)
    task_id: Optional[str] = None
    subtask_id: Optional[str] = None
    approval_id: Optional[str] = Field(default=None, description="Pass approved approval_id to execute a Tier-3 command")


def _record_permission_approval(
    tool_category: str,
    requested_action: str,
    workspace_path: str,
    risk_reason: str,
    task_id: Optional[str] = None,
    subtask_id: Optional[str] = None,
) -> Dict[str, Any]:
    approval = {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "subtask_id": subtask_id,
        "tool_category": tool_category,
        "requested_action": requested_action,
        "workspace_path": workspace_path,
        "risk_reason": risk_reason,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state = load_local_state()
    state.setdefault("permission_approvals", []).insert(0, approval)
    save_local_state(state)

    if pg_available():
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO permission_approvals
                        (id, tool_category, requested_action, workspace_path, risk_reason, status)
                        VALUES (%s, %s, %s, %s, %s, 'pending')
                        """,
                        (approval["id"], tool_category, requested_action, workspace_path, risk_reason),
                    )
                conn.commit()
        except Exception:
            pass
    return approval


@app.post("/tools/shell/exec")
def tool_shell_exec(req: ShellExecRequest) -> Dict[str, Any]:
    cwd = resolve_and_validate_path(req.workspace_path, require_write=False)
    tier, reason = classify_shell_command(req.command)

    if tier == "BLOCKED":
        raise HTTPException(status_code=403, detail=f"Command BLOCKED by security policy: {reason}")

    if tier == "APPROVAL_REQUIRED":
        # Check if user already approved via approval_id
        approved = False
        if req.approval_id:
            state = load_local_state()
            for item in state.get("permission_approvals", []):
                if item["id"] == req.approval_id and item["status"] == "approved":
                    approved = True
                    break
        if not approved:
            approval = _record_permission_approval(
                tool_category="shell_exec",
                requested_action=req.command,
                workspace_path=str(cwd),
                risk_reason=reason,
                task_id=req.task_id,
                subtask_id=req.subtask_id,
            )
            return {
                "status": "awaiting_approval",
                "permission_tier": tier,
                "reason": reason,
                "approval_id": approval["id"],
                "message": "Command paused and queued on Developer Mission Control Board for 1-click user approval.",
            }

    start_ts = time.time()
    env = os.environ.copy()
    venv_bin = str(BASE_DIR / "host-bridge" / ".venv" / "bin")
    env["PATH"] = f"{venv_bin}:/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{BASE_DIR / 'host-bridge'}:{env.get('PYTHONPATH', '')}"
    try:
        proc = subprocess.run(
            req.command,
            shell=True,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=req.timeout_seconds,
        )
        duration_ms = int((time.time() - start_ts) * 1000)
        return {
            "status": "executed",
            "permission_tier": tier,
            "command": req.command,
            "workspace_path": str(cwd),
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
            "stdout": proc.stdout[-15000:],
            "stderr": proc.stderr[-15000:],
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail=f"Command timed out after {req.timeout_seconds}s")


@app.get("/tools/shell/processes")
def tool_shell_processes() -> Dict[str, Any]:
    proc = subprocess.run(
        "lsof -iTCP -sTCP:LISTEN -P -n | head -n 40",
        shell=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return {
        "listening_ports": proc.stdout.strip().splitlines(),
    }


# ============================================================================
# 4. Database Inspection & Query Suite (/tools/db/*)
# ============================================================================

class DbQueryRequest(BaseModel):
    sql: str
    params: Optional[List[Any]] = None
    approval_id: Optional[str] = None


@app.get("/tools/db/schema")
def tool_db_schema() -> Dict[str, Any]:
    if pg_available():
        try:
            with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name, column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                        ORDER BY table_name, ordinal_position
                        """
                    )
                    rows = cur.fetchall()
            tables: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                tables.setdefault(r["table_name"], []).append({
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"],
                })
            return {"engine": "postgresql+pgvector", "tables": tables}
        except Exception as exc:
            pass

    # Fallback schema description from db/init.sql when Postgres container isn't started yet
    return {
        "engine": "schema-catalog-fallback",
        "tables": {
            "dev_tasks": [
                {"column": "id", "type": "uuid"},
                {"column": "title", "type": "text"},
                {"column": "prompt", "type": "text"},
                {"column": "workspace_path", "type": "text"},
                {"column": "status", "type": "text"},
                {"column": "orchestrator_provider", "type": "text"},
                {"column": "orchestrator_model", "type": "text"},
                {"column": "worker_model", "type": "text"},
                {"column": "execution_plan", "type": "jsonb"},
                {"column": "final_output", "type": "text"},
            ],
            "dev_subtasks": [
                {"column": "id", "type": "uuid"},
                {"column": "task_id", "type": "uuid"},
                {"column": "step_order", "type": "int"},
                {"column": "title", "type": "text"},
                {"column": "assigned_specialist", "type": "text"},
                {"column": "status", "type": "text"},
                {"column": "tool_calls", "type": "jsonb"},
                {"column": "result_output", "type": "text"},
            ],
            "agent_error_lessons": [
                {"column": "id", "type": "uuid"},
                {"column": "domain", "type": "text"},
                {"column": "failure_signature", "type": "text"},
                {"column": "failed_action", "type": "text"},
                {"column": "root_cause", "type": "text"},
                {"column": "learned_rule", "type": "text"},
                {"column": "embedding", "type": "vector(768)"},
            ],
            "agent_skills_rag": [
                {"column": "id", "type": "uuid"},
                {"column": "skill_name", "type": "text"},
                {"column": "description", "type": "text"},
                {"column": "system_instructions", "type": "text"},
                {"column": "allowed_tools", "type": "jsonb"},
                {"column": "embedding", "type": "vector(768)"},
            ],
            "knowledge_chunks_rag": [
                {"column": "id", "type": "uuid"},
                {"column": "source_type", "type": "text"},
                {"column": "source_uri", "type": "text"},
                {"column": "title", "type": "text"},
                {"column": "content_markdown", "type": "text"},
                {"column": "embedding", "type": "vector(768)"},
            ],
        },
    }


@app.post("/tools/db/query")
def tool_db_query(req: DbQueryRequest) -> Dict[str, Any]:
    tier, reason = classify_sql_statement(req.sql)
    if tier == "APPROVAL_REQUIRED" and not req.approval_id:
        approval = _record_permission_approval(
            tool_category="db_query",
            requested_action=req.sql,
            workspace_path="PostgreSQL: n8n_orchestrator",
            risk_reason=reason,
        )
        return {
            "status": "awaiting_approval",
            "permission_tier": tier,
            "reason": reason,
            "approval_id": approval["id"],
        }

    if not pg_available():
        return {
            "status": "offline_fallback",
            "permission_tier": tier,
            "note": "PostgreSQL container is not reachable yet; query validated against policy.",
            "sql": req.sql,
        }

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(req.sql, req.params or None)
            if cur.description:
                rows = cur.fetchall()
                return {"status": "ok", "permission_tier": tier, "row_count": len(rows), "rows": rows[:200]}
            conn.commit()
            return {"status": "ok", "permission_tier": tier, "affected_rows": cur.rowcount}


# ============================================================================
# 5. Self-Learning Episodic & Procedural RAG Engine (/tools/rag/*)
# ============================================================================

class RagRecallRequest(BaseModel):
    task_prompt: str
    domain: Optional[str] = None
    top_k_lessons: int = Field(default=4, ge=1, le=15)
    top_k_skills: int = Field(default=3, ge=1, le=10)
    top_k_docs: int = Field(default=4, ge=1, le=15)


class RagLearnErrorRequest(BaseModel):
    task_id: Optional[str] = None
    domain: str = Field(..., description="e.g. web_crawler, local_fs, sql_query, shell_cmd, code_gen")
    failure_signature: str
    failed_action: str
    error_trace: str
    root_cause: Optional[str] = None
    learned_rule: Optional[str] = None
    verified_fix: Optional[str] = None


class IngestLocalSkillsRequest(BaseModel):
    skills_dir: str = Field(default=SKILLS_DIR)
    max_skills: int = Field(default=25, ge=1, le=200)
    filter_keyword: Optional[str] = Field(default=None)


@app.post("/tools/rag/recall")
async def tool_rag_recall(req: RagRecallRequest) -> Dict[str, Any]:
    """
    Called by n8n Orchestrator BEFORE planning or executing a task.
    Retrieves:
      1. Relevant Past Error Lessons (so the agent never repeats a mistake)
      2. Relevant Procedural Skills & Tool Playbooks
      3. Relevant Crawled Documentation Chunks
    """
    q_vec = await compute_embedding(req.task_prompt)
    state = load_local_state()

    lessons = state.get("agent_error_lessons", [])
    skills = state.get("agent_skills_rag", [])
    docs = state.get("knowledge_chunks_rag", [])

    scored_lessons = []
    for item in lessons:
        if req.domain and item.get("domain") != req.domain:
            continue
        text = f"{item.get('domain', '')} {item.get('failure_signature', '')} {item.get('learned_rule', '')}"
        emb = item.get("embedding") or await compute_embedding(text)
        score = 0.65 * cosine_similarity(q_vec, emb) + 0.35 * lexical_overlap_score(req.task_prompt, text)
        scored_lessons.append({**{k: v for k, v in item.items() if k != "embedding"}, "similarity": round(score, 4)})

    scored_skills = []
    for sk in skills:
        text = f"{sk.get('skill_name', '')} {sk.get('description', '')} {sk.get('system_instructions', '')}"
        emb = sk.get("embedding") or await compute_embedding(text)
        score = 0.65 * cosine_similarity(q_vec, emb) + 0.35 * lexical_overlap_score(req.task_prompt, text)
        scored_skills.append({**{k: v for k, v in sk.items() if k != "embedding"}, "similarity": round(score, 4)})

    scored_docs = []
    for doc in docs:
        text = f"{doc.get('title', '')} {doc.get('content_markdown', '')}"
        emb = doc.get("embedding") or await compute_embedding(text)
        score = 0.65 * cosine_similarity(q_vec, emb) + 0.35 * lexical_overlap_score(req.task_prompt, text)
        scored_docs.append({**{k: v for k, v in doc.items() if k != "embedding"}, "similarity": round(score, 4)})

    scored_lessons.sort(key=lambda x: x["similarity"], reverse=True)
    scored_skills.sort(key=lambda x: x["similarity"], reverse=True)
    scored_docs.sort(key=lambda x: x["similarity"], reverse=True)

    top_lessons = scored_lessons[: req.top_k_lessons]
    top_skills = scored_skills[: req.top_k_skills]
    top_docs = scored_docs[: req.top_k_docs]

    compiled_prompt_guardrails = "\n".join(
        [f"- [{l['domain'].upper()}] {l['learned_rule']}" for l in top_lessons]
    )

    return {
        "query": req.task_prompt,
        "compiled_guardrails": compiled_prompt_guardrails,
        "error_lessons": top_lessons,
        "matched_skills": top_skills,
        "knowledge_chunks": top_docs,
    }


@app.post("/tools/rag/learn-error")
async def tool_rag_learn_error(req: RagLearnErrorRequest) -> Dict[str, Any]:
    """
    Self-Learning Episodic Error Loop:
    Takes a tool/agent error trace, distills a root cause & actionable rule
    (via Local Ollama if not explicitly provided), embeds it with nomic-embed-text,
    and saves it to agent_error_lessons in pgvector + local state.
    """
    root_cause = req.root_cause
    learned_rule = req.learned_rule

    if not root_cause or not learned_rule:
        # Try asking local Ollama worker to synthesize the rule
        synthesis_prompt = (
            f"Analyze this AI Agent failure and write a 1-sentence root cause and a 1-sentence actionable RULE "
            f"to prevent it in future runs.\nDomain: {req.domain}\nFailed Action: {req.failed_action}\n"
            f"Error Trace: {req.error_trace}\nRespond in JSON: {{\"root_cause\": \"...\", \"learned_rule\": \"RULE: ...\"}}"
        )
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": OLLAMA_WORKER_MODEL, "prompt": synthesis_prompt, "stream": False, "format": "json"},
                )
                if resp.status_code == 200:
                    parsed = json.loads(resp.json().get("response", "{}"))
                    root_cause = root_cause or parsed.get("root_cause")
                    learned_rule = learned_rule or parsed.get("learned_rule")
        except Exception:
            pass

    root_cause = root_cause or f"Execution failed during {req.domain} operation ({req.failure_signature})."
    learned_rule = learned_rule or (
        f"RULE: When performing '{req.domain}', avoid '{req.failed_action}' which triggers '{req.failure_signature}'. "
        f"Verify inputs/schema first."
    )

    embed_text = f"{req.domain} {req.failure_signature} {req.failed_action} {root_cause} {learned_rule}"
    emb = await compute_embedding(embed_text)

    lesson = {
        "id": str(uuid.uuid4()),
        "task_id": req.task_id,
        "domain": req.domain,
        "failure_signature": req.failure_signature,
        "failed_action": req.failed_action,
        "error_trace": req.error_trace,
        "root_cause": root_cause,
        "learned_rule": learned_rule,
        "verified_fix": req.verified_fix or "",
        "embedding": emb,
        "hit_count": 1,
        "confidence_score": 0.95,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    state = load_local_state()
    state.setdefault("agent_error_lessons", []).insert(0, lesson)
    save_local_state(state)

    if pg_available():
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_error_lessons
                        (id, domain, failure_signature, failed_action, error_trace, root_cause, learned_rule, verified_fix, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        """,
                        (
                            lesson["id"],
                            lesson["domain"],
                            lesson["failure_signature"],
                            lesson["failed_action"],
                            lesson["error_trace"],
                            lesson["root_cause"],
                            lesson["learned_rule"],
                            lesson["verified_fix"],
                            json.dumps(emb),
                        ),
                    )
                conn.commit()
        except Exception:
            pass

    return {
        "status": "learned",
        "lesson_id": lesson["id"],
        "domain": lesson["domain"],
        "root_cause": lesson["root_cause"],
        "learned_rule": lesson["learned_rule"],
    }


@app.post("/tools/rag/ingest-local-skills")
async def tool_rag_ingest_local_skills(req: IngestLocalSkillsRequest) -> Dict[str, Any]:
    """
    Scans local SKILL.md files (configured via SKILLS_DIR)
    and indexes them into agent_skills_rag so n8n AI Agents can dynamically load them via RAG!
    """
    skills_root = resolve_and_validate_path(req.skills_dir, require_write=False)
    if not skills_root.exists():
        raise HTTPException(status_code=404, detail=f"Skills directory not found: {skills_root}")

    ingested: List[Dict[str, Any]] = []
    state = load_local_state()
    existing_by_name = {s["skill_name"]: s for s in state.get("agent_skills_rag", [])}

    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    for sf in skill_files:
        skill_folder_name = sf.parent.name
        if req.filter_keyword and req.filter_keyword.lower() not in skill_folder_name.lower():
            continue
        try:
            raw = sf.read_text(encoding="utf-8", errors="ignore")
            desc_match = re.search(r"description:\s*[\"']?([^\"'\n]+)[\"']?", raw)
            description = desc_match.group(1).strip() if desc_match else f"Specialized skill {skill_folder_name}"
            body = re.sub(r"^---[\s\S]*?---\s*", "", raw).strip()[:3000]
            emb = await compute_embedding(f"{skill_folder_name} {description}\n{body[:1500]}")

            record = {
                "id": str(uuid.uuid4()),
                "skill_name": skill_folder_name,
                "category": "imported-skill",
                "description": description,
                "system_instructions": body,
                "allowed_tools": ["fs_tree", "fs_grep", "fs_read", "fs_patch", "web_search", "web_crawl_page", "shell_exec"],
                "source_path": str(sf),
                "embedding": emb,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            existing_by_name[skill_folder_name] = record
            ingested.append({"skill_name": skill_folder_name, "description": description})
            if len(ingested) >= req.max_skills:
                break
        except Exception:
            continue

    state["agent_skills_rag"] = list(existing_by_name.values())
    save_local_state(state)
    return {
        "status": "ingested",
        "count": len(ingested),
        "skills": ingested,
    }


# ============================================================================
# 6. Developer Mission Control Board API (/api/board/*)
# ============================================================================

class CreateBoardTaskRequest(BaseModel):
    title: str
    prompt: str
    workspace_path: str = Field(default=str(BASE_DIR))
    orchestrator_provider: str = Field(default="gemini", description="gemini | claude | openai")
    orchestrator_model: str = Field(default="gemini-2.5-pro")
    worker_model: str = Field(default="qwen2.5-coder:7b")
    permissions_profile: Dict[str, bool] = Field(
        default_factory=lambda: {"web_crawl": True, "fs_write": True, "db_write": True, "shell_exec": True}
    )
    auto_dispatch_to_n8n: bool = Field(default=True)


class UpdateTaskStatusRequest(BaseModel):
    status: str
    execution_plan: Optional[List[Dict[str, Any]]] = None
    final_output: Optional[str] = None
    error_summary: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")


@app.get("/", response_class=HTMLResponse)
def serve_mission_control_dashboard() -> HTMLResponse:
    dash_file = BASE_DIR / "host-bridge" / "dashboard.html"
    if dash_file.exists():
        return HTMLResponse(content=dash_file.read_text(encoding="utf-8"), status_code=200)
    return HTMLResponse(content="<h1>Dashboard HTML missing</h1>", status_code=404)


@app.get("/api/board/workflows")
def api_board_workflows() -> Dict[str, Any]:
    wf_dir = BASE_DIR / "workflows"
    items: List[Dict[str, Any]] = []
    for wf_path in sorted(wf_dir.glob("*.json")):
        try:
            data = json.loads(wf_path.read_text(encoding="utf-8"))
            nodes = data.get("nodes", [])
            items.append({
                "file": wf_path.name,
                "name": data.get("name", wf_path.stem),
                "node_count": len(nodes),
                "agent_node_count": sum(1 for n in nodes if "langchain" in n.get("type", "")),
                "nodes": [
                    {
                        "name": n.get("name"),
                        "type": n.get("type"),
                        "is_ai": "langchain" in n.get("type", ""),
                    }
                    for n in nodes
                ],
            })
        except Exception:
            continue
    return {"workflows": items}


@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "n8n-personal-agent-host-bridge",
        "postgres_connected": pg_available(),
        "ollama_url": OLLAMA_URL,
        "searxng_url": SEARXNG_URL,
        "crawl4ai_url": CRAWL4AI_URL,
        "workspace_root": str(BASE_DIR),
    }


@app.get("/api/board/state")
def api_board_state() -> Dict[str, Any]:
    state = load_local_state()
    tasks = state.get("dev_tasks", [])
    subtasks = state.get("dev_subtasks", [])
    approvals = state.get("permission_approvals", [])
    lessons = [
        {k: v for k, v in l.items() if k != "embedding"}
        for l in state.get("agent_error_lessons", [])
    ]
    skills = [
        {k: v for k, v in s.items() if k != "embedding"}
        for s in state.get("agent_skills_rag", [])
    ]
    chunks = [
        {k: v for k, v in c.items() if k != "embedding"}
        for c in state.get("knowledge_chunks_rag", [])
    ]
    return {
        "tasks": tasks,
        "subtasks": subtasks,
        "approvals": approvals,
        "error_lessons": lessons,
        "skills": skills,
        "knowledge_chunks": chunks,
        "services": {
            "postgres_rag": pg_available(),
            "host_bridge": True,
            "ollama_worker_model": OLLAMA_WORKER_MODEL,
            "ollama_embed_model": OLLAMA_EMBED_MODEL,
            "workspace_root": str(WORKSPACE_ROOT),
            "skills_dir": SKILLS_DIR,
        },
    }


@app.post("/api/board/tasks")
async def api_create_board_task(req: CreateBoardTaskRequest) -> Dict[str, Any]:
    # 1. Perform automatic pre-task RAG recall so card immediately shows matched skills & guardrails
    rag = await tool_rag_recall(RagRecallRequest(task_prompt=f"{req.title}\n{req.prompt}"))
    guardrails = [l["learned_rule"] for l in rag["error_lessons"][:3]]

    # 2. Decompose prompt into initial hybrid Orchestrator -> Local Worker subtasks
    subtask_specs = [
        {
            "step": 1,
            "specialist": "web_researcher" if any(w in req.prompt.lower() for w in ("web", "crawl", "doc", "url", "search", "ara")) else "local_coder",
            "title": "Context Discovery & Web/Codebase Research",
            "prompt_payload": f"Discover required context for: {req.title}. Apply RAG rules: {guardrails}",
        },
        {
            "step": 2,
            "specialist": "local_coder",
            "title": f"Implementation via Local Worker ({req.worker_model})",
            "prompt_payload": f"Execute core development prompt: {req.prompt}",
        },
        {
            "step": 3,
            "specialist": "pc_shell_runner",
            "title": "Automated Verification & Test Execution",
            "prompt_payload": "Run unit tests / verification checks via Host Bridge and trigger Self-Learning RAG on any error.",
        },
    ]

    task_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    new_task = {
        "id": task_id,
        "title": req.title,
        "prompt": req.prompt,
        "workspace_path": req.workspace_path,
        "status": "orchestrating" if req.auto_dispatch_to_n8n else "queued",
        "orchestrator_provider": req.orchestrator_provider,
        "orchestrator_model": req.orchestrator_model,
        "worker_model": req.worker_model,
        "permissions_profile": req.permissions_profile,
        "rag_context_injected": guardrails,
        "execution_plan": subtask_specs,
        "final_output": None,
        "token_metrics": {"paid_input_tokens": 640, "paid_output_tokens": 210, "local_tokens": 0, "estimated_saved_usd": 0.14},
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    state = load_local_state()
    state.setdefault("dev_tasks", []).insert(0, new_task)

    created_subtasks = []
    for spec in subtask_specs:
        st = {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "step_order": spec["step"],
            "title": spec["title"],
            "assigned_specialist": spec["specialist"],
            "model_tier": "local",
            "prompt_payload": spec["prompt_payload"],
            "status": "pending",
            "tool_calls": [],
            "result_output": None,
            "retry_count": 0,
            "created_at": now_iso,
        }
        state.setdefault("dev_subtasks", []).append(st)
        created_subtasks.append(st)

    save_local_state(state)

    # 3. Try triggering n8n Master Orchestrator webhook if n8n container is active
    n8n_dispatched = False
    if req.auto_dispatch_to_n8n:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(
                    f"{N8N_WEBHOOK_BASE}/webhook/dev-task",
                    json={"task": new_task, "subtasks": created_subtasks, "rag": rag},
                )
                n8n_dispatched = resp.status_code < 400
        except Exception:
            n8n_dispatched = False

    return {
        "task": new_task,
        "subtasks": created_subtasks,
        "rag_recall": rag,
        "n8n_webhook_dispatched": n8n_dispatched,
    }


@app.patch("/api/board/tasks/{task_id}")
def api_update_task_status(task_id: str, req: UpdateTaskStatusRequest) -> Dict[str, Any]:
    state = load_local_state()
    for task in state.get("dev_tasks", []):
        if task["id"] == task_id:
            task["status"] = req.status
            if req.execution_plan is not None:
                task["execution_plan"] = req.execution_plan
            if req.final_output is not None:
                task["final_output"] = req.final_output
            if req.error_summary is not None:
                task["error_summary"] = req.error_summary
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_local_state(state)
            return {"status": "updated", "task": task}
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.post("/api/board/approvals/{approval_id}/decide")
def api_decide_approval(approval_id: str, req: ApprovalDecisionRequest) -> Dict[str, Any]:
    state = load_local_state()
    for item in state.get("permission_approvals", []):
        if item["id"] == approval_id:
            item["status"] = req.decision
            item["decided_at"] = datetime.now(timezone.utc).isoformat()
            exec_res = None
            if req.decision == "approved" and item["tool_category"] == "shell_exec":
                exec_res = tool_shell_exec(
                    ShellExecRequest(
                        command=item["requested_action"],
                        workspace_path=item.get("workspace_path") or str(BASE_DIR),
                        approval_id=approval_id,
                    )
                )
                item["execution_result"] = exec_res
            save_local_state(state)
            return {"status": req.decision, "approval": item, "execution_result": exec_res}
    raise HTTPException(status_code=404, detail=f"Approval request not found: {approval_id}")

