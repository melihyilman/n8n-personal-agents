-- ============================================================================
-- Unified PostgreSQL + pgvector Schema
-- Developer Mission Control Board + n8n Memory + Self-Learning RAG + File RAG
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Developer Board Tasks (Top-Level Orchestration Cards)
CREATE TABLE IF NOT EXISTS dev_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    workspace_path TEXT NOT NULL DEFAULT '.',
    status TEXT NOT NULL DEFAULT 'queued',
    -- Allowed statuses:
    --   queued, orchestrating, worker_loop, verifying, learning, awaiting_approval, completed, failed
    orchestrator_provider TEXT NOT NULL DEFAULT 'gemini', -- gemini | claude | openai
    orchestrator_model TEXT NOT NULL DEFAULT 'gemini-2.5-pro',
    worker_model TEXT NOT NULL DEFAULT 'qwen2.5-coder:7b',
    permissions_profile JSONB NOT NULL DEFAULT '{"web_crawl": true, "fs_write": true, "db_write": true, "shell_exec": true}'::jsonb,
    rag_context_injected JSONB DEFAULT '[]'::jsonb,
    execution_plan JSONB DEFAULT '[]'::jsonb,
    final_output TEXT,
    error_summary TEXT,
    token_metrics JSONB DEFAULT '{"paid_input_tokens": 0, "paid_output_tokens": 0, "local_tokens": 0, "estimated_saved_usd": 0.0}'::jsonb,
    n8n_execution_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Sub-Tasks Delegated by Paid Orchestrator to Local LLM Workers
CREATE TABLE IF NOT EXISTS dev_subtasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES dev_tasks(id) ON DELETE CASCADE,
    step_order INT NOT NULL,
    title TEXT NOT NULL,
    assigned_specialist TEXT NOT NULL DEFAULT 'local_coder',
    -- local_coder | web_researcher | db_operator | pc_shell_runner | verifier
    model_tier TEXT NOT NULL DEFAULT 'local', -- local (Ollama) | paid (Orchestrator escalation)
    prompt_payload TEXT NOT NULL,
    expected_artifact TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending | running | awaiting_approval | completed | failed | self_healed
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_output TEXT,
    error_log TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Permission Approval Gate Queue (For Tier 3 APPROVAL_REQUIRED Actions)
CREATE TABLE IF NOT EXISTS permission_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES dev_tasks(id) ON DELETE CASCADE,
    subtask_id UUID REFERENCES dev_subtasks(id) ON DELETE SET NULL,
    tool_category TEXT NOT NULL, -- shell_exec | fs_write | db_query
    requested_action TEXT NOT NULL,
    workspace_path TEXT,
    risk_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    decided_by TEXT,
    execution_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ
);

-- 4. Self-Learning Episodic Error RAG Memory (nomic-embed-text 768 dimensions)
CREATE TABLE IF NOT EXISTS agent_error_lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES dev_tasks(id) ON DELETE SET NULL,
    domain TEXT NOT NULL,
    -- e.g., 'web_crawler', 'local_fs', 'sql_query', 'shell_cmd', 'code_gen', 'n8n_workflow'
    failure_signature TEXT NOT NULL,
    failed_action TEXT NOT NULL,
    error_trace TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    learned_rule TEXT NOT NULL,
    verified_fix TEXT,
    embedding vector(768),
    hit_count INT NOT NULL DEFAULT 1,
    confidence_score FLOAT NOT NULL DEFAULT 0.9,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_error_lessons_domain ON agent_error_lessons(domain);
CREATE INDEX IF NOT EXISTS idx_error_lessons_embedding
    ON agent_error_lessons USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- 5. Procedural Skills & Tool Playbooks RAG
CREATE TABLE IF NOT EXISTS agent_skills_rag (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_name TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    description TEXT NOT NULL,
    system_instructions TEXT NOT NULL,
    allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_path TEXT,
    embedding vector(768),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_skills_rag_embedding
    ON agent_skills_rag USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- 6. Crawled Web Documentation & Local Workspace File Chunks RAG
CREATE TABLE IF NOT EXISTS knowledge_chunks_rag (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL, -- 'web_crawl' | 'local_file' | 'manual_note'
    source_uri TEXT NOT NULL,
    title TEXT,
    chunk_index INT NOT NULL DEFAULT 0,
    content_markdown TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_uri ON knowledge_chunks_rag(source_uri);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
    ON knowledge_chunks_rag USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 7. Seed Initial Core Skills & Baseline Self-Learning Lessons
INSERT INTO agent_skills_rag (skill_name, category, description, system_instructions, allowed_tools)
VALUES
(
    'hybrid-code-implementer',
    'engineering',
    'Local LLM specialist for inspecting local codebases, applying surgical patches, and running unit tests.',
    'Always run fs_tree or fs_grep before editing files. Use fs_read with line ranges to inspect exact context, apply changes via fs_patch, and immediately verify using shell_exec with pytest/npm test.',
    '["fs_tree", "fs_grep", "fs_read", "fs_patch", "fs_write", "shell_exec"]'::jsonb
),
(
    'deep-web-researcher',
    'research',
    'Autonomous web search and headless browser crawler that extracts clean Markdown and indexes docs into pgvector.',
    'Start with web_search (SearXNG) to discover official documentation URLs. Then call web_crawl_page for single pages or web_deep_crawl to ingest multi-page documentation into knowledge_chunks_rag.',
    '["web_search", "web_crawl_page", "web_deep_crawl", "rag_query"]'::jsonb
),
(
    'database-architect-operator',
    'database',
    'Database schema inspector and safe parameterized SQL executor.',
    'Never guess column names. Always call db_schema first to inspect tables, columns, and types before running db_query. Use parameterized queries.',
    '["db_schema", "db_query", "rag_query"]'::jsonb
)
ON CONFLICT (skill_name) DO NOTHING;

INSERT INTO agent_error_lessons (domain, failure_signature, failed_action, error_trace, root_cause, learned_rule, verified_fix)
VALUES
(
    'sql_query',
    'column does not exist in dev_tasks',
    'SELECT task_name FROM dev_tasks',
    'psycopg2.errors.UndefinedColumn: column "task_name" does not exist',
    'Agent guessed column name task_name instead of inspecting the schema first.',
    'RULE: Before writing any SQL query against a table, ALWAYS call /tools/db/schema or query information_schema.columns to verify exact column names (e.g. dev_tasks uses "title", not "task_name").',
    'SELECT id, title, status FROM dev_tasks'
),
(
    'web_crawler',
    'Empty content from SPA documentation page',
    'Simple HTTP GET on client-rendered docs page',
    'Extracted text length < 50 chars (only <div id="root"></div>)',
    'Target website uses client-side React/Next.js rendering which raw HTTP GET cannot parse.',
    'RULE: When scraping modern documentation or JS-rendered websites, ALWAYS use /tools/web/crawl-page (Crawl4AI / Playwright headless browser) with wait_for_js=true instead of raw HTTP GET.',
    'POST /tools/web/crawl-page {"url": "...", "wait_for_js": true}'
),
(
    'local_fs',
    'Patch target mismatch due to stale line contents',
    'POST /tools/fs/patch with guessed whitespace',
    'ValueError: Target block not found in file',
    'Agent attempted to patch a file without reading the current lines first.',
    'RULE: Always call /tools/fs/read on the target line range immediately before calling /tools/fs/patch so whitespace and indentation match 100%.',
    'Call /tools/fs/read first, then copy exact target string into /tools/fs/patch'
);

