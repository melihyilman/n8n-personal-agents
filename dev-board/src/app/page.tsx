"use client";

import React, { useEffect, useState, useCallback } from "react";

const HOST_BRIDGE_API =
  process.env.NEXT_PUBLIC_HOST_BRIDGE_URL || "http://localhost:8090";

interface Subtask {
  id: string;
  task_id: string;
  step_order: number;
  title: string;
  assigned_specialist: string;
  model_tier: string;
  prompt_payload: string;
  status: string;
  result_output?: string;
}

interface DevTask {
  id: string;
  title: string;
  prompt: string;
  workspace_path: string;
  status: string;
  orchestrator_provider: string;
  orchestrator_model: string;
  worker_model: string;
  permissions_profile: Record<string, boolean>;
  rag_context_injected: string[];
  execution_plan: Array<{ step: number; specialist: string; title: string; status?: string }>;
  final_output?: string;
  token_metrics?: {
    paid_input_tokens: number;
    paid_output_tokens: number;
    local_tokens: number;
    estimated_saved_usd: number;
  };
  created_at: string;
}

interface ApprovalItem {
  id: string;
  tool_category: string;
  requested_action: string;
  workspace_path: string;
  risk_reason: string;
  status: string;
  created_at: string;
}

interface ErrorLesson {
  id: string;
  domain: string;
  failure_signature: string;
  failed_action: string;
  root_cause: string;
  learned_rule: string;
  verified_fix?: string;
  hit_count: number;
  confidence_score: number;
}

interface SkillItem {
  id: string;
  skill_name: string;
  category: string;
  description: string;
  system_instructions: string;
  allowed_tools: string[];
}

const KANBAN_COLUMNS = [
  { key: "queued", label: "1. Queued Prompts", badgeColor: "bg-slate-700 text-slate-200" },
  { key: "orchestrating", label: "2. Paid Orchestrator (Plan & RAG)", badgeColor: "bg-indigo-600/30 text-indigo-300 border border-indigo-500/40" },
  { key: "worker_loop", label: "3. Local Ollama Worker Loop", badgeColor: "bg-emerald-600/30 text-emerald-300 border border-emerald-500/40" },
  { key: "learning", label: "4. Verification & Self-Learning RAG", badgeColor: "bg-amber-600/30 text-amber-300 border border-amber-500/40" },
  { key: "completed", label: "5. Completed & Verified", badgeColor: "bg-cyan-600/30 text-cyan-300 border border-cyan-500/40" },
];

const ORCHESTRATOR_OPTIONS: Record<string, string[]> = {
  gemini: ["gemini-2.5-pro", "gemini-2.5-flash"],
  claude: ["claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219", "claude-3-5-haiku-latest"],
  openai: ["gpt-4o", "o3-mini", "gpt-4.1"],
};

export default function DeveloperMissionControlBoard() {
  const [activeTab, setActiveTab] = useState<"kanban" | "tools" | "rag" | "approvals">("kanban");
  const [tasks, setTasks] = useState<DevTask[]>([]);
  const [subtasks, setSubtasks] = useState<Subtask[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [lessons, setLessons] = useState<ErrorLesson[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusBanner, setStatusBanner] = useState<string | null>(null);

  // New Task Composer State
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [provider, setProvider] = useState<"gemini" | "claude" | "openai">("gemini");
  const [orchestratorModel, setOrchestratorModel] = useState("gemini-2.5-pro");
  const [workerModel, setWorkerModel] = useState("qwen2.5-coder:7b");
  const [perms, setPerms] = useState({
    web_crawl: true,
    fs_write: true,
    db_write: true,
    shell_exec: true,
  });

  // Live Sandbox Tool Tester State
  const [sandboxTool, setSandboxTool] = useState<"web_search" | "web_crawl" | "fs_grep" | "fs_semantic" | "shell_exec">("web_search");
  const [sandboxInput, setSandboxInput] = useState("n8n AI Agent toolWorkflow LangChain documentation");
  const [sandboxOutput, setSandboxOutput] = useState<string>("");

  // Manual Self-Learning Error Form State
  const [errDomain, setErrDomain] = useState("web_crawler");
  const [errSignature, setErrSignature] = useState("");
  const [errAction, setErrAction] = useState("");
  const [errTrace, setErrTrace] = useState("");
  const [errRule, setErrRule] = useState("");

  const fetchBoardState = useCallback(async () => {
    try {
      const res = await fetch(`${HOST_BRIDGE_API}/api/board/state`);
      if (!res.ok) return;
      const data = await res.json();
      setTasks(data.tasks || []);
      setSubtasks(data.subtasks || []);
      setApprovals(data.approvals || []);
      setLessons(data.error_lessons || []);
      setSkills(data.skills || []);
    } catch {
      // Host bridge offline notice handled gracefully
    }
  }, []);

  useEffect(() => {
    fetchBoardState();
    const timer = setInterval(fetchBoardState, 5000);
    return () => clearInterval(timer);
  }, [fetchBoardState]);

  const handleCreateTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !prompt.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${HOST_BRIDGE_API}/api/board/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          prompt,
          workspace_path: workspacePath,
          orchestrator_provider: provider,
          orchestrator_model: orchestratorModel,
          worker_model: workerModel,
          permissions_profile: perms,
          auto_dispatch_to_n8n: true,
        }),
      });
      const data = await res.json();
      setTitle("");
      setPrompt("");
      setStatusBanner(
        `Task launched! Pre-Task RAG injected ${data.task?.rag_context_injected?.length || 0} self-learned error guardrails.`
      );
      await fetchBoardState();
    } finally {
      setLoading(false);
    }
  };

  const handleMoveTask = async (taskId: string, nextStatus: string) => {
    await fetch(`${HOST_BRIDGE_API}/api/board/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: nextStatus }),
    });
    await fetchBoardState();
  };

  const handleRunSandboxTool = async () => {
    setSandboxOutput("Executing tool against macOS Host Bridge...");
    try {
      let endpoint = "/tools/web/search";
      let payload: Record<string, unknown> = { query: sandboxInput, max_results: 5 };

      if (sandboxTool === "web_crawl") {
        endpoint = "/tools/web/crawl-page";
        payload = { url: sandboxInput, wait_for_js: true, index_into_rag: true };
      } else if (sandboxTool === "fs_grep") {
        endpoint = "/tools/fs/grep";
        payload = { workspace_path: workspacePath, pattern: sandboxInput };
      } else if (sandboxTool === "fs_semantic") {
        endpoint = "/tools/fs/semantic-search";
        payload = { workspace_path: workspacePath, query: sandboxInput, top_k: 5 };
      } else if (sandboxTool === "shell_exec") {
        endpoint = "/tools/shell/exec";
        payload = { workspace_path: workspacePath, command: sandboxInput };
      }

      const res = await fetch(`${HOST_BRIDGE_API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      setSandboxOutput(JSON.stringify(data, null, 2));
      await fetchBoardState();
    } catch (err) {
      setSandboxOutput(`Error calling Host Bridge: ${String(err)}`);
    }
  };

  const handleTeachErrorLesson = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!errSignature.trim()) return;
    await fetch(`${HOST_BRIDGE_API}/tools/rag/learn-error`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: errDomain,
        failure_signature: errSignature,
        failed_action: errAction || "Agent tool execution",
        error_trace: errTrace || errSignature,
        learned_rule: errRule || undefined,
      }),
    });
    setErrSignature("");
    setErrAction("");
    setErrTrace("");
    setErrRule("");
    setStatusBanner("New episodic error lesson embedded with nomic-embed-text and stored in pgvector!");
    await fetchBoardState();
  };

  const handleIngestLocalSkills = async () => {
    setStatusBanner("Ingesting local SKILL.md files from SKILLS_DIR into pgvector...");
    const res = await fetch(`${HOST_BRIDGE_API}/tools/rag/ingest-local-skills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        skills_dir: "~/.gemini/config/skills",
        max_skills: 30,
      }),
    });
    const data = await res.json();
    setStatusBanner(`Ingested ${data.count || 0} local skills into agent_skills_rag!`);
    await fetchBoardState();
  };

  const handleApprovalDecision = async (approvalId: string, decision: "approved" | "rejected") => {
    await fetch(`${HOST_BRIDGE_API}/api/board/approvals/${approvalId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    setStatusBanner(`Permission request ${decision}.`);
    await fetchBoardState();
  };

  const pendingApprovalsCount = approvals.filter((a) => a.status === "pending").length;

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top Header Bar */}
      <header className="border-b border-slate-800 bg-[#0d1322]/90 backdrop-blur px-6 py-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <span className="h-3 w-3 rounded-full bg-emerald-400 animate-pulse" />
            <h1 className="text-xl font-bold tracking-tight text-white">
              n8n Personal AI Agent Orchestrator & Developer Board
            </h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Paid LLM Orchestrator (Gemini / Claude / OpenAI) → Local Ollama Worker Loop (qwen2.5-coder:7b) → Self-Learning pgvector RAG
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab("kanban")}
            className={`px-3.5 py-2 rounded-lg text-xs font-medium transition ${
              activeTab === "kanban"
                ? "bg-indigo-600 text-white"
                : "bg-slate-800/80 text-slate-300 hover:bg-slate-700"
            }`}
          >
            Kanban & Prompt Launcher ({tasks.length})
          </button>
          <button
            onClick={() => setActiveTab("tools")}
            className={`px-3.5 py-2 rounded-lg text-xs font-medium transition ${
              activeTab === "tools"
                ? "bg-indigo-600 text-white"
                : "bg-slate-800/80 text-slate-300 hover:bg-slate-700"
            }`}
          >
            Live Web Crawler & Local File Tools
          </button>
          <button
            onClick={() => setActiveTab("rag")}
            className={`px-3.5 py-2 rounded-lg text-xs font-medium transition ${
              activeTab === "rag"
                ? "bg-indigo-600 text-white"
                : "bg-slate-800/80 text-slate-300 hover:bg-slate-700"
            }`}
          >
            Self-Learning RAG Brain ({lessons.length} Rules / {skills.length} Skills)
          </button>
          <button
            onClick={() => setActiveTab("approvals")}
            className={`px-3.5 py-2 rounded-lg text-xs font-medium transition flex items-center gap-1.5 ${
              activeTab === "approvals"
                ? "bg-amber-600 text-white"
                : "bg-slate-800/80 text-slate-300 hover:bg-slate-700"
            }`}
          >
            PC Permission Gate
            {pendingApprovalsCount > 0 && (
              <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-rose-500 text-white font-bold">
                {pendingApprovalsCount}
              </span>
            )}
          </button>
        </div>
      </header>

      {statusBanner && (
        <div className="bg-indigo-950/90 border-b border-indigo-700/50 px-6 py-2.5 text-xs text-indigo-200 flex items-center justify-between">
          <span>{statusBanner}</span>
          <button onClick={() => setStatusBanner(null)} className="text-indigo-400 hover:text-white">
            Dismiss
          </button>
        </div>
      )}

      {/* Main Content */}
      <main className="flex-1 p-6 space-y-6">
        {activeTab === "kanban" && (
          <>
            {/* Development Prompt Launcher */}
            <section className="bg-[#111827] border border-slate-800 rounded-xl p-5 shadow-lg">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-slate-200">
                  Launch New Automated Development Task (Dispatches to n8n Master Orchestrator Webhook)
                </h2>
                <span className="text-[11px] text-emerald-400 bg-emerald-950/60 border border-emerald-700/40 px-2.5 py-0.5 rounded-full">
                  Pre-Task RAG Recall Active
                </span>
              </div>

              <form onSubmit={handleCreateTask} className="grid grid-cols-1 lg:grid-cols-12 gap-4">
                <div className="lg:col-span-4 space-y-3">
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Task Title</label>
                    <input
                      type="text"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="e.g., Crawl Stripe Webhook Docs & Patch FastAPI Handler"
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:border-indigo-500 focus:outline-none"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Target Local Workspace Path</label>
                    <input
                      type="text"
                      value={workspacePath}
                      onChange={(e) => setWorkspacePath(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200"
                    />
                  </div>

                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">Paid Orchestrator</label>
                      <select
                        value={provider}
                        onChange={(e) => {
                          const nextProv = e.target.value as "gemini" | "claude" | "openai";
                          setProvider(nextProv);
                          setOrchestratorModel(ORCHESTRATOR_OPTIONS[nextProv][0]);
                        }}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-2 text-xs text-white"
                      >
                        <option value="gemini">Google Gemini</option>
                        <option value="claude">Anthropic Claude</option>
                        <option value="openai">OpenAI</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">Orchestrator Model</label>
                      <select
                        value={orchestratorModel}
                        onChange={(e) => setOrchestratorModel(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-2 text-xs text-white"
                      >
                        {ORCHESTRATOR_OPTIONS[provider].map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">Local Worker (Ollama)</label>
                      <select
                        value={workerModel}
                        onChange={(e) => setWorkerModel(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-2 text-xs text-emerald-300"
                      >
                        <option value="qwen2.5-coder:7b">qwen2.5-coder:7b</option>
                        <option value="qwen2.5-coder:14b">qwen2.5-coder:14b</option>
                        <option value="llama3.1:8b">llama3.1:8b</option>
                        <option value="deepseek-r1:8b">deepseek-r1:8b</option>
                      </select>
                    </div>
                  </div>
                </div>

                <div className="lg:col-span-5 flex flex-col">
                  <label className="block text-[11px] text-slate-400 mb-1">
                    Development Prompt (Decomposed by Paid LLM → Executed by Local LLM + Tools)
                  </label>
                  <textarea
                    rows={5}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="Describe the feature, bugfix, or web research task. The Paid Orchestrator will query pgvector for past error lessons, plan the subtasks, and delegate coding/crawling/testing to your local Ollama agent..."
                    className="w-full flex-1 bg-slate-900 border border-slate-700 rounded-lg p-3 text-xs text-white focus:border-indigo-500 focus:outline-none"
                    required
                  />
                </div>

                <div className="lg:col-span-3 flex flex-col justify-between bg-slate-900/70 border border-slate-800 rounded-lg p-3">
                  <div>
                    <span className="block text-[11px] font-semibold text-slate-300 mb-2">
                      Agent Tool & PC Permissions
                    </span>
                    <div className="space-y-1.5 text-xs">
                      {[
                        { key: "web_crawl", label: "Web Search (SearXNG) & Crawl4AI" },
                        { key: "fs_write", label: "Local File Read / Surgical Patch" },
                        { key: "db_write", label: "PostgreSQL + pgvector Read/Write" },
                        { key: "shell_exec", label: "Host PC Terminal & Test Runner" },
                      ].map((item) => (
                        <label key={item.key} className="flex items-center gap-2 text-slate-300 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={perms[item.key as keyof typeof perms]}
                            onChange={(e) =>
                              setPerms((prev) => ({ ...prev, [item.key]: e.target.checked }))
                            }
                            className="rounded border-slate-600 bg-slate-800 text-indigo-500"
                          />
                          <span>{item.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="mt-4 w-full py-2.5 px-4 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-semibold text-white shadow transition"
                  >
                    {loading ? "Orchestrating..." : "Launch Agent Orchestration Flow"}
                  </button>
                </div>
              </form>
            </section>

            {/* 5-Column Kanban Board */}
            <section className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4">
              {KANBAN_COLUMNS.map((col) => {
                const colTasks = tasks.filter((t) => t.status === col.key);
                return (
                  <div
                    key={col.key}
                    className="bg-[#111827]/90 border border-slate-800 rounded-xl p-3.5 flex flex-col min-h-[480px]"
                  >
                    <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800">
                      <span className={`text-xs font-semibold px-2.5 py-1 rounded-md ${col.badgeColor}`}>
                        {col.label}
                      </span>
                      <span className="text-xs font-mono text-slate-400">{colTasks.length}</span>
                    </div>

                    <div className="space-y-3 flex-1">
                      {colTasks.map((task) => {
                        const taskSubtasks = subtasks.filter((s) => s.task_id === task.id);
                        return (
                          <div
                            key={task.id}
                            className="bg-slate-900/90 border border-slate-700/80 hover:border-indigo-500/60 rounded-lg p-3.5 space-y-2.5 transition shadow"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <h3 className="text-xs font-bold text-white leading-snug">{task.title}</h3>
                              <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-700/40 shrink-0">
                                {task.orchestrator_provider}
                              </span>
                            </div>

                            <p className="text-[11px] text-slate-300 line-clamp-3">{task.prompt}</p>

                            <div className="flex flex-wrap gap-1.5 text-[10px]">
                              <span className="px-2 py-0.5 rounded bg-slate-800 text-indigo-300">
                                Plan: {task.orchestrator_model}
                              </span>
                              <span className="px-2 py-0.5 rounded bg-emerald-950/70 text-emerald-300 border border-emerald-700/30">
                                Worker: {task.worker_model}
                              </span>
                            </div>

                            {/* Injected Self-Learned RAG Rules */}
                            {task.rag_context_injected && task.rag_context_injected.length > 0 && (
                              <div className="bg-amber-950/30 border border-amber-700/30 rounded p-2 space-y-1">
                                <div className="text-[10px] font-semibold text-amber-300">
                                  Injected Self-Learned RAG Guardrails ({task.rag_context_injected.length})
                                </div>
                                {task.rag_context_injected.slice(0, 2).map((rule, idx) => (
                                  <p key={idx} className="text-[10px] text-amber-200/90 leading-tight">
                                    • {rule}
                                  </p>
                                ))}
                              </div>
                            )}

                            {/* Delegated Sub-Tasks */}
                            {taskSubtasks.length > 0 && (
                              <div className="space-y-1 pt-1 border-t border-slate-800">
                                <span className="text-[10px] font-semibold text-slate-400">
                                  Local Worker Sub-Tasks ({taskSubtasks.length}):
                                </span>
                                {taskSubtasks.map((st) => (
                                  <div
                                    key={st.id}
                                    className="text-[10px] bg-slate-950/70 px-2 py-1 rounded flex items-center justify-between gap-1"
                                  >
                                    <span className="text-slate-300 truncate">
                                      {st.step_order}. [{st.assigned_specialist}] {st.title}
                                    </span>
                                    <span className="text-emerald-400 font-mono shrink-0">{st.status}</span>
                                  </div>
                                ))}
                              </div>
                            )}

                            {task.token_metrics && (
                              <div className="text-[10px] text-slate-400 flex items-center justify-between pt-1 border-t border-slate-800/80">
                                <span>Local Tokens: {task.token_metrics.local_tokens}</span>
                                <span className="text-emerald-400 font-semibold">
                                  Saved ~${task.token_metrics.estimated_saved_usd.toFixed(2)}
                                </span>
                              </div>
                            )}

                            {/* Stage Controls */}
                            <div className="flex items-center justify-between gap-1 pt-2 border-t border-slate-800">
                              {KANBAN_COLUMNS.map((targetCol) =>
                                targetCol.key !== task.status ? (
                                  <button
                                    key={targetCol.key}
                                    onClick={() => handleMoveTask(task.id, targetCol.key)}
                                    className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 hover:bg-indigo-600 text-slate-300 hover:text-white transition"
                                  >
                                    → {targetCol.key}
                                  </button>
                                ) : null
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </section>
          </>
        )}

        {activeTab === "tools" && (
          <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-5 bg-[#111827] border border-slate-800 rounded-xl p-5 space-y-4">
              <h2 className="text-sm font-bold text-white">
                Interactive Agent Toolset Sandbox (Web Search, Crawl4AI & Local File Intelligence)
              </h2>
              <p className="text-xs text-slate-400">
                Test the exact tool endpoints that your n8n Local Ollama Worker Agent invokes during autonomous loops.
              </p>

              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-slate-300 mb-1">Select Capability Tool</label>
                  <select
                    value={sandboxTool}
                    onChange={(e) => {
                      const val = e.target.value as typeof sandboxTool;
                      setSandboxTool(val);
                      if (val === "web_search") setSandboxInput("n8n langchain agent toolWorkflow");
                      if (val === "web_crawl") setSandboxInput("https://docs.n8n.io/");
                      if (val === "fs_grep") setSandboxInput("tool_rag_learn_error");
                      if (val === "fs_semantic") setSandboxInput("how does self learning error RAG embedding work");
                      if (val === "shell_exec") setSandboxInput("git status");
                    }}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white"
                  >
                    <option value="web_search">1. Web Search (SearXNG / DuckDuckGo Live Search)</option>
                    <option value="web_crawl">2. Headless Web Crawler (Crawl4AI + Auto pgvector Indexing)</option>
                    <option value="fs_grep">3. Local Codebase Ripgrep Search (/tools/fs/grep)</option>
                    <option value="fs_semantic">4. Local File Semantic Vector Search (/tools/fs/semantic-search)</option>
                    <option value="shell_exec">5. Host PC Terminal Runner (/tools/shell/exec with 3-Tier Policy)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs text-slate-300 mb-1">Tool Input (Query / URL / Regex / Command)</label>
                  <input
                    type="text"
                    value={sandboxInput}
                    onChange={(e) => setSandboxInput(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-white"
                  />
                </div>

                <button
                  onClick={handleRunSandboxTool}
                  className="w-full py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold text-white transition"
                >
                  Execute Tool via Host Bridge
                </button>
              </div>
            </div>

            <div className="lg:col-span-7 bg-[#111827] border border-slate-800 rounded-xl p-5 flex flex-col">
              <h3 className="text-xs font-semibold text-slate-300 mb-2">Live Tool Response Payload</h3>
              <pre className="flex-1 bg-slate-950 border border-slate-800 rounded-lg p-4 text-[11px] font-mono text-emerald-300 overflow-auto max-h-[520px]">
                {sandboxOutput || "// Select a tool on the left and click Execute to inspect live output..."}
              </pre>
            </div>
          </section>
        )}

        {activeTab === "rag" && (
          <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Teach New Lesson + Ingest Local Skills */}
            <div className="lg:col-span-4 space-y-6">
              <div className="bg-[#111827] border border-slate-800 rounded-xl p-5 space-y-3">
                <h2 className="text-sm font-bold text-white">Teach Personal Agent a New Error Lesson</h2>
                <p className="text-xs text-slate-400">
                  When an agent makes a mistake or you want to enforce a permanent engineering rule, record it here. It is embedded with <code className="text-emerald-300">nomic-embed-text</code> and injected into future prompts.
                </p>
                <form onSubmit={handleTeachErrorLesson} className="space-y-2.5 text-xs">
                  <div>
                    <label className="block text-slate-400 mb-1">Domain</label>
                    <select
                      value={errDomain}
                      onChange={(e) => setErrDomain(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white"
                    >
                      <option value="web_crawler">web_crawler</option>
                      <option value="local_fs">local_fs</option>
                      <option value="sql_query">sql_query</option>
                      <option value="shell_cmd">shell_cmd</option>
                      <option value="code_gen">code_gen</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-slate-400 mb-1">Failure Signature / Mistake</label>
                    <input
                      type="text"
                      value={errSignature}
                      onChange={(e) => setErrSignature(e.target.value)}
                      placeholder="e.g., Timeout scraping dynamic React table"
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-slate-400 mb-1">Failed Action</label>
                    <input
                      type="text"
                      value={errAction}
                      onChange={(e) => setErrAction(e.target.value)}
                      placeholder="e.g., GET without wait_for_js"
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-slate-400 mb-1">
                      Actionable Rule (Leave blank for Local Ollama to auto-synthesize)
                    </label>
                    <textarea
                      rows={3}
                      value={errRule}
                      onChange={(e) => setErrRule(e.target.value)}
                      placeholder="RULE: Always..."
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white"
                    />
                  </div>
                  <button
                    type="submit"
                    className="w-full py-2 px-4 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold transition"
                  >
                    Embed & Save to Episodic Error RAG
                  </button>
                </form>
              </div>

              <div className="bg-[#111827] border border-slate-800 rounded-xl p-5 space-y-3">
                <h3 className="text-sm font-bold text-white">Sync Local Skill Library into pgvector</h3>
                <p className="text-xs text-slate-400">
                  Index your local <code className="text-indigo-300">$SKILLS_DIR/*/SKILL.md</code> playbooks into <code className="text-emerald-300">agent_skills_rag</code> so n8n agents automatically retrieve matching skills per task.
                </p>
                <button
                  onClick={handleIngestLocalSkills}
                  className="w-full py-2.5 px-4 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white transition"
                >
                  Ingest Local Skills ($SKILLS_DIR)
                </button>
              </div>
            </div>

            {/* Learned Error Rules & Procedural Skills List */}
            <div className="lg:col-span-8 space-y-6">
              <div className="bg-[#111827] border border-slate-800 rounded-xl p-5">
                <h3 className="text-sm font-bold text-amber-300 mb-3">
                  Episodic Error Lessons in RAG Memory ({lessons.length})
                </h3>
                <div className="space-y-3">
                  {lessons.map((l) => (
                    <div
                      key={l.id}
                      className="bg-slate-900 border border-slate-800 rounded-lg p-3.5 space-y-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-white">
                          [{l.domain.toUpperCase()}] {l.failure_signature}
                        </span>
                        <span className="text-[10px] font-mono text-slate-400">
                          Hits: {l.hit_count} | Confidence: {(l.confidence_score * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="text-xs text-amber-300 font-medium">{l.learned_rule}</p>
                      <p className="text-[11px] text-slate-400">
                        Root Cause: {l.root_cause} | Failed Action: <code>{l.failed_action}</code>
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-[#111827] border border-slate-800 rounded-xl p-5">
                <h3 className="text-sm font-bold text-indigo-300 mb-3">
                  Procedural Skills & Tool Playbooks in RAG ({skills.length})
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {skills.map((sk) => (
                    <div
                      key={sk.id}
                      className="bg-slate-900 border border-slate-800 rounded-lg p-3.5 space-y-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-white">{sk.skill_name}</span>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-950 text-indigo-300">
                          {sk.category}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-300">{sk.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>
        )}

        {activeTab === "approvals" && (
          <section className="bg-[#111827] border border-slate-800 rounded-xl p-5 space-y-4">
            <div>
              <h2 className="text-sm font-bold text-white">
                Host PC & Database Tier-3 Permission Approval Gate (APPROVAL_REQUIRED)
              </h2>
              <p className="text-xs text-slate-400 mt-1">
                Commands matching high-risk patterns in <code>config/permissions.yaml</code> (such as <code>rm -rf</code>, <code>git push</code>, <code>DROP/DELETE</code>) pause execution and appear here for your explicit 1-click decision.
              </p>
            </div>

            {approvals.length === 0 ? (
              <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-8 text-center text-xs text-slate-400">
                No high-risk commands awaiting approval. Tier-1 (AUTO_SAFE) and Tier-2 (WORKSPACE_WRITE) actions execute automatically.
              </div>
            ) : (
              <div className="space-y-3">
                {approvals.map((item) => (
                  <div
                    key={item.id}
                    className="bg-slate-900 border border-slate-700 rounded-lg p-4 flex flex-wrap items-center justify-between gap-4"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold uppercase px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-700/40">
                          {item.tool_category}
                        </span>
                        <span className="text-xs font-mono text-white">{item.requested_action}</span>
                      </div>
                      <p className="text-[11px] text-slate-400">
                        Workspace: <code>{item.workspace_path}</code> | Reason: {item.risk_reason}
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      {item.status === "pending" ? (
                        <>
                          <button
                            onClick={() => handleApprovalDecision(item.id, "approved")}
                            className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold text-white"
                          >
                            Approve & Execute
                          </button>
                          <button
                            onClick={() => handleApprovalDecision(item.id, "rejected")}
                            className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-xs font-semibold text-white"
                          >
                            Reject
                          </button>
                        </>
                      ) : (
                        <span
                          className={`text-xs font-bold uppercase px-2.5 py-1 rounded ${
                            item.status === "approved"
                              ? "bg-emerald-950 text-emerald-300"
                              : "bg-rose-950 text-rose-300"
                          }`}
                        >
                          {item.status}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

