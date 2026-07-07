"""Dependency-free local web UI for interactive research and trace inspection."""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from google import genai
from google.genai import types

from .agent import run_turn
from .deep_research import (
    pending_checkpoint,
    resume_deep_research,
    select_research_direction,
    start_deep_research,
)
from .runtime import now, token_usage
from .logging_config import get_logger, log, logs_after

RUN_ID = re.compile(r"^[0-9a-f-]{36}$")
OPERATIONS: dict[str, dict] = {}
ACTIVE_RUNS: set[str] = set()
LOCK = threading.Lock()
LOGGER = get_logger("gui")

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Multi-Agent Console</title>
  <style>
    :root { color-scheme: dark; --bg:#0b1020; --panel:#121a2e; --line:#293653;
      --text:#e8edf7; --muted:#9aa8c2; --accent:#7aa2ff; --good:#5ad6a0; }
    * { box-sizing:border-box; } body { margin:0; font:14px/1.5 Inter,system-ui,sans-serif;
      background:radial-gradient(circle at 50% -20%,#182442 0,var(--bg) 36%); color:var(--text); }
    header { height:76px; display:flex; align-items:center; padding:0 22px;
      border-bottom:1px solid var(--line); background:rgba(8,13,26,.88); backdrop-filter:blur(14px); }
    h1 { margin:0; font-size:20px; } .muted { color:var(--muted); }
    .brand { display:flex; gap:12px; align-items:center; }
    .mark { width:34px; height:34px; border-radius:10px; display:grid; place-items:center;
      background:linear-gradient(135deg,#7aa2ff,#a77aff); color:#07101f; font-weight:900; }
    .layout { display:grid; grid-template-columns:280px minmax(420px,1fr) 360px;
      gap:14px; height:calc(100vh - 76px); padding:14px; }
    .layout.debug-collapsed { grid-template-columns:54px minmax(420px,1fr) 360px; }
    .sidebar,.logs-pane,.workspace { min-height:0; }
    .workspace { overflow:auto; padding:0 4px 40px; }
    .sidebar,.logs-pane { background:rgba(12,19,36,.92); border:1px solid var(--line);
      border-radius:14px; overflow:hidden; display:flex; flex-direction:column; }
    .pane-head { display:flex; align-items:center; justify-content:space-between; padding:13px;
      border-bottom:1px solid var(--line); font-weight:800; }
    .icon-btn { color:var(--text); background:#1b2945; padding:7px 10px; }
    .layout.debug-collapsed .sidebar-body,.layout.debug-collapsed .pane-title { display:none; }
    .layout.debug-collapsed .pane-head { justify-content:center; padding:9px 5px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:14px;
      padding:18px; margin-top:18px; }
    textarea,input { width:100%; color:var(--text); background:#0c1427;
      border:1px solid var(--line); border-radius:9px; padding:11px; }
    textarea { min-height:100px; resize:vertical; }
    button { color:#08101f; background:var(--accent); border:0; border-radius:9px;
      padding:10px 15px; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.5; cursor:wait; }
    .option { display:block; width:100%; text-align:left; margin:9px 0;
      background:#1b2945; color:var(--text); border:1px solid #38517c; }
    .option:hover { border-color:var(--accent); }
    .row { display:flex; gap:12px; align-items:center; margin-top:12px; }
    .row input[type=checkbox] { width:auto; }
    .status { color:var(--good); font-weight:700; }
    #report { white-space:pre-wrap; overflow-wrap:anywhere; }
    #debug { height:100%; overflow:auto; background:#070c18; padding:12px;
      font:11px/1.45 ui-monospace,monospace; white-space:pre-wrap; }
    .sidebar-body { flex:1; min-height:0; }
    #logs { flex:1; overflow:auto; padding:10px; font:11px/1.45 ui-monospace,monospace; }
    .log { padding:8px 9px; margin-bottom:7px; border-left:3px solid #64748b;
      border-radius:5px; background:#09101e; overflow-wrap:anywhere; }
    .log .meta { color:var(--muted); margin-bottom:3px; }
    .log.DEBUG { border-color:#64748b; }.log.INFO { border-color:#5ad6a0; }
    .log.WARN,.log.WARNING { border-color:#f4c95d; }.log.ERROR { border-color:#ff718b; }
    .log.FATAL { border-color:#ff3d65; background:#260d18; }
    .pill { padding:3px 7px; border-radius:999px; background:#1a2742; color:var(--muted);
      font-size:11px; }
    .tabs { display:flex; gap:8px; margin-bottom:14px; position:sticky; top:0;
      z-index:3; padding:4px; background:var(--bg); border-radius:11px; }
    .tab { background:#17233d; color:var(--muted); }
    .tab.active { background:var(--accent); color:#08101f; }
    .view { display:none; }.view.active { display:block; }
    .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    select { width:100%; padding:10px; color:var(--text); background:#0c1427;
      border:1px solid var(--line); border-radius:9px; }
    table { width:100%; border-collapse:collapse; font-size:12px; }
    th,td { text-align:left; padding:9px; border-bottom:1px solid var(--line);
      vertical-align:top; } th { color:var(--muted); position:sticky; top:0; background:var(--panel); }
    .table-wrap { max-height:430px; overflow:auto; border:1px solid var(--line); border-radius:9px; }
    .check-list { max-height:220px; overflow:auto; background:#0c1427;
      border:1px solid var(--line); border-radius:9px; padding:9px; }
    .check-list label { display:block; padding:5px; }.check-list input { width:auto; margin-right:8px; }
    .result-card { white-space:pre-wrap; background:#09101e; border-radius:9px; padding:12px; }
    .hidden { display:none; }
    @media(max-width:1000px) {
      .layout,.layout.debug-collapsed { grid-template-columns:1fr; height:auto; }
      .sidebar,.logs-pane { min-height:300px; }.layout.debug-collapsed .sidebar { min-height:54px; }
    }
  </style>
</head>
<body>
<header><div class="brand"><div class="mark">A</div><div>
  <h1>Multi-Agent Console</h1>
  <div class="muted">Dynamic planning, specialist delegation, and live observability</div>
</div></div></header>
<div id="layout" class="layout">
  <aside class="sidebar">
    <div class="pane-head"><span class="pane-title">Debug inspector</span>
      <button id="debugToggle" class="icon-btn" title="Collapse debug panel">⇤</button></div>
    <div class="sidebar-body"><div id="debug">No trace selected yet.</div></div>
  </aside>

  <main class="workspace">
  <nav class="tabs">
    <button class="tab active" data-view="agentView">Agent</button>
    <button class="tab" data-view="evalView">Evaluations</button>
    <button class="tab" data-view="datasetView">Dataset Builder</button>
  </nav>
  <div id="agentView" class="view active">
  <section class="panel" style="margin-top:0">
    <label for="query"><strong>What can the agents help with?</strong></label>
    <textarea id="query" placeholder="Ask any question, request analysis, inspect the codebase, or begin deep research..."></textarea>
    <div class="row">
      <button id="start">Ask agents</button>
      <span id="status" class="status">Ready</span>
    </div>
  </section>

  <section id="choicesPanel" class="panel hidden">
    <strong>Choose the next research direction</strong>
    <div id="choices"></div>
    <label for="feedback">Optional guidance or correction</label>
    <input id="feedback" placeholder="For example: focus on India and use primary sources">
  </section>

  <section id="reportPanel" class="panel hidden">
    <strong id="answerTitle">Answer</strong>
    <div id="report"></div>
  </section>
  </div>

  <div id="evalView" class="view">
    <section class="panel" style="margin-top:0">
      <h2 style="margin-top:0">Phoenix Evaluations</h2>
      <p class="muted">Run the real multi-agent application against selected examples and publish scores and traces to Phoenix.</p>
      <div class="grid2">
        <label>Run limit
          <input id="evalLimit" type="number" min="1" placeholder="Blank runs selected examples">
        </label>
        <label>Qualitative judge
          <select id="evalJudge"><option value="false">Deterministic evaluators only</option>
            <option value="true">Include LLM-as-judge</option></select>
        </label>
      </div>
      <p><strong>Select examples</strong></p>
      <div id="evalChecks" class="check-list">Loading dataset…</div>
      <div class="row">
        <button id="runEval">Run Phoenix evaluation</button>
        <span id="evalStatus" class="status">Ready</span>
      </div>
    </section>
    <section id="evalResultPanel" class="panel hidden">
      <strong>Experiment result</strong>
      <div id="evalResult" class="result-card"></div>
    </section>
  </div>

  <div id="datasetView" class="view">
    <section class="panel" style="margin-top:0">
      <h2 style="margin-top:0">Evaluation Dataset</h2>
      <p class="muted">Browse committed examples or add a new routing/tool expectation from the UI.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>ID</th><th>Prompt</th><th>Route</th><th>Agents</th><th>Tools</th></tr></thead>
        <tbody id="datasetRows"></tbody>
      </table></div>
    </section>
    <section class="panel">
      <h3 style="margin-top:0">Create example</h3>
      <div class="grid2">
        <label>Stable ID<input id="dsId" placeholder="my_new_example"></label>
        <label>Expected mode<select id="dsMode">
          <option>direct</option><option>single</option><option>parallel</option><option>sequential</option>
        </select></label>
      </div>
      <label>Prompt<textarea id="dsPrompt" placeholder="The user request to evaluate"></textarea></label>
      <div class="grid2">
        <label>Expected agents<input id="dsAgents" placeholder="research_agent,data_science_agent"></label>
        <label>Expected tools<input id="dsTools" placeholder="web_search,run_python"></label>
      </div>
      <label>Reference answer or behavior<textarea id="dsReference" placeholder="What a correct result should establish"></textarea></label>
      <div class="grid2">
        <label>Category<input id="dsCategory" placeholder="routing, safety, grounding"></label>
        <label>Difficulty<select id="dsDifficulty"><option>easy</option><option selected>medium</option><option>hard</option></select></label>
      </div>
      <div class="row"><button id="saveExample">Save example</button>
        <span id="datasetStatus" class="status"></span></div>
    </section>
  </div>

  </main>

  <aside class="logs-pane">
    <div class="pane-head"><span>Live logs</span><span id="logCount" class="pill">0</span></div>
    <div id="logs"></div>
  </aside>
</div>
<script>
let runId = null, operationId = null, busy = false;
let sessionId = localStorage.getItem("agentSessionId") || crypto.randomUUID();
let latestLogSequence = 0, logCount = 0, lastState = null;
let datasetExamples = [];
localStorage.setItem("agentSessionId", sessionId);
const $ = id => document.getElementById(id);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function setBusy(value, message) {
  busy = value; $("start").disabled = value;
  document.querySelectorAll(".option").forEach(button => button.disabled = value);
  $("status").textContent = message;
}

async function api(path, options={}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type":"application/json", ...(options.headers || {})}
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function render(state) {
  lastState = state;
  if (state.kind === "evaluation") {
    $("evalStatus").textContent = "Completed";
    $("runEval").disabled = false;
    $("evalResultPanel").classList.remove("hidden");
    $("evalResult").innerHTML = "";
    [
      ["Experiment", state.experiment_name],
      ["Dataset", state.dataset_name],
      ["Examples", state.example_count],
      ["Model", state.model],
      ["LLM judge", state.judge_enabled ? "enabled" : "disabled"]
    ].forEach(([label, value]) => {
      const line = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = label + ": ";
      line.append(strong, document.createTextNode(String(value)));
      $("evalResult").appendChild(line);
    });
    const link = document.createElement("a");
    link.href = state.phoenix_url; link.target = "_blank"; link.rel = "noopener";
    link.textContent = "Open experiment workspace in Phoenix";
    link.style.color = "var(--accent)";
    $("evalResult").appendChild(link);
    renderDebug(state);
    return;
  }
  if (state.kind === "research") runId = state.run_id || runId;
  $("status").textContent = `${state.status || "running"}${state.route ? " · " + state.route : ""}`;
  $("choices").innerHTML = "";
  if (state.kind === "research" && state.status === "waiting_for_human") {
    $("choicesPanel").classList.remove("hidden");
    state.options.forEach((option, index) => {
      const button = document.createElement("button");
      button.className = "option";
      button.textContent = `${index + 1}. ${option.label} — ${option.outcome}`;
      button.onclick = () => choose(index + 1);
      $("choices").appendChild(button);
    });
  } else {
    $("choicesPanel").classList.add("hidden");
  }
  if (state.status === "completed") {
    $("reportPanel").classList.remove("hidden");
    $("answerTitle").textContent = state.kind === "research" ? "Final report" : "Answer";
    $("report").textContent = state.kind === "research" ? state.final_report : state.answer;
  }
  renderDebug(state);
}

function renderDebug(state) {
  $("debug").textContent = JSON.stringify({
    kind: state.kind,
    route: state.route,
    route_reason: state.route_reason,
    session_id: state.session_id,
    run_id: state.run_id,
    status: state.status,
    token_estimate: state.token_estimate,
    source_count: state.source_count,
    compaction_count: state.compaction_count,
    current_focus: state.current_focus,
    unresolved_questions: state.unresolved_questions,
    selected_directions: state.selected_directions,
    checkpoints: state.checkpoints,
    agent_summaries: state.agent_summaries,
    tokens: state.tokens,
    events: state.events
  }, null, 2);
  $("debug").scrollTop = $("debug").scrollHeight;
}

async function pollOperation() {
  while (operationId) {
    const operation = await api(`/api/operations/${operationId}`);
    if (operation.status === "completed") {
      operationId = null; setBusy(false, "Completed"); render(operation.result); return;
    }
    if (operation.status === "failed") {
      operationId = null; setBusy(false, "Failed");
      $("runEval").disabled = false;
      $("evalStatus").textContent = "Failed";
      throw new Error(operation.error);
    }
    if (runId) {
      try { render(await api(`/api/research/${runId}`)); } catch (_) {}
    }
    await sleep(700);
  }
}

async function start() {
  const query = $("query").value.trim();
  if (!query || busy) return;
  runId = null;
  $("choicesPanel").classList.add("hidden");
  setBusy(true, "Planning and selecting agents...");
  try {
    const operation = await api("/api/query", {
      method:"POST", body:JSON.stringify({query, session_id:sessionId})
    });
    operationId = operation.operation_id;
    await pollOperation();
  } catch (error) { setBusy(false, "Error: " + error.message); }
}

async function choose(optionNumber) {
  if (!runId || busy) return;
  setBusy(true, "Research specialist is working...");
  try {
    const operation = await api("/api/research/select", {
      method:"POST",
      body:JSON.stringify({
        run_id:runId, option_number:optionNumber, feedback:$("feedback").value
      })
    });
    $("feedback").value = "";
    operationId = operation.operation_id;
    await pollOperation();
  } catch (error) { setBusy(false, "Error: " + error.message); }
}

$("start").onclick = start;
$("runEval").onclick = async () => {
  const ids = [...document.querySelectorAll("#evalChecks input:checked")].map(item => item.value);
  const limitValue = $("evalLimit").value.trim();
  $("runEval").disabled = true;
  $("evalStatus").textContent = "Running real agent examples…";
  try {
    const operation = await api("/api/evals/run", {
      method:"POST",
      body:JSON.stringify({
        ids,
        limit:limitValue ? Number(limitValue) : null,
        judge:$("evalJudge").value === "true"
      })
    });
    operationId = operation.operation_id;
    await pollOperation();
  } catch (error) {
    $("runEval").disabled = false;
    $("evalStatus").textContent = "Error: " + error.message;
  }
};

document.querySelectorAll(".tab").forEach(tabButton => {
  tabButton.onclick = () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach(item => item.classList.remove("active"));
    tabButton.classList.add("active");
    $(tabButton.dataset.view).classList.add("active");
  };
});

function csv(value) {
  return value.split(",").map(item => item.trim()).filter(Boolean);
}

function renderDataset(examples) {
  datasetExamples = examples;
  $("datasetRows").innerHTML = "";
  $("evalChecks").innerHTML = "";
  examples.forEach(example => {
    const row = document.createElement("tr");
    [example.id, example.prompt, example.expected.mode,
      (example.expected.agents || []).join(", "),
      (example.expected.tools || []).join(", ")].forEach(value => {
        const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
      });
    $("datasetRows").appendChild(row);
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox"; check.value = example.id; check.checked = true;
    label.append(check, document.createTextNode(`${example.id} — ${example.prompt}`));
    $("evalChecks").appendChild(label);
  });
}

async function loadDataset() {
  const result = await api("/api/evals/dataset");
  renderDataset(result.examples);
}

$("saveExample").onclick = async () => {
  $("datasetStatus").textContent = "Saving…";
  try {
    await api("/api/evals/dataset", {
      method:"POST",
      body:JSON.stringify({
        id:$("dsId").value,
        prompt:$("dsPrompt").value,
        expected:{
          mode:$("dsMode").value,
          agents:csv($("dsAgents").value),
          tools:csv($("dsTools").value),
          reference:$("dsReference").value
        },
        metadata:{category:$("dsCategory").value, difficulty:$("dsDifficulty").value}
      })
    });
    $("datasetStatus").textContent = "Saved";
    await loadDataset();
  } catch (error) { $("datasetStatus").textContent = "Error: " + error.message; }
};

$("debugToggle").onclick = () => {
  const collapsed = $("layout").classList.toggle("debug-collapsed");
  $("debugToggle").textContent = collapsed ? "⇥" : "⇤";
};

function appendLogs(records) {
  records.forEach(record => {
    const item = document.createElement("div");
    item.className = `log ${record.level}`;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${record.timestamp.slice(11,19)} · ${record.level} · ${record.logger}`;
    const message = document.createElement("div");
    message.textContent = record.message;
    item.append(meta, message);
    if (record.fields && Object.keys(record.fields).length) {
      const fields = document.createElement("div");
      fields.className = "muted";
      fields.textContent = JSON.stringify(record.fields);
      item.appendChild(fields);
    }
    $("logs").appendChild(item);
    latestLogSequence = record.sequence;
    logCount++;
  });
  while ($("logs").children.length > 400) $("logs").firstChild.remove();
  $("logCount").textContent = logCount;
  if (records.length) $("logs").scrollTop = $("logs").scrollHeight;
}

async function pollLogs() {
  try {
    const batch = await api(`/api/logs?after=${latestLogSequence}`);
    appendLogs(batch.records);
    latestLogSequence = Math.max(latestLogSequence, batch.latest_sequence);
  } catch (_) {}
}
setInterval(pollLogs, 500);
pollLogs();
loadDataset().catch(error => { $("datasetStatus").textContent = "Error: " + error.message; });
</script></body></html>"""


def _public_run(run: dict) -> dict:
    checkpoint = pending_checkpoint(run)
    return {
        "kind": "research",
        "route": "deep_research_agent",
        "route_reason": "Iterative research with human-selected directions.",
        "run_id": run["run_id"],
        "status": run["status"],
        "current_focus": run["current_focus"],
        "unresolved_questions": run["unresolved_questions"],
        "selected_directions": run["selected_directions"],
        "token_estimate": run["token_estimate"],
        "source_count": len(run["source_ledger"]),
        "compaction_count": len(run["compaction_history"]),
        "options": checkpoint["options"] if checkpoint else [],
        "checkpoints": run["checkpoints"],
        "events": run["events"],
        "final_report": run["final_report"],
    }


def _public_turn(result: dict, session_id: str, route: dict) -> dict:
    trajectory = result["trajectory"]
    return {
        "kind": "chat",
        "route": route["mode"],
        "route_reason": route["reason"],
        "session_id": session_id,
        "status": "completed",
        "answer": result["answer"],
        "artifacts": result["artifacts"],
        "agent_summaries": trajectory["agent_summaries"],
        "tokens": trajectory["tokens"],
        "events": trajectory["events"],
    }


def _route_query(api_key: str, model: str, query: str) -> dict:
    """Choose the interactive deep-research loop or the general coordinator."""
    log(LOGGER, "INFO", "Routing user query", query_characters=len(query))
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=query,
        config=types.GenerateContentConfig(
            system_instruction=(
                "Route one user request. Return JSON with mode and reason. Use mode "
                "'deep_research' only when the user explicitly asks for deep/comprehensive "
                "research, or when the vague question needs iterative web investigation and "
                "human direction choices. Otherwise use mode 'general_coordinator'. The "
                "general coordinator can answer directly or dynamically delegate current "
                "information to research_agent, repository questions to codebase_agent, and "
                "calculation/data work to data_science_agent."
            ),
            response_mime_type="application/json",
        ),
    )
    try:
        value = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        value = {}
    mode = str(value.get("mode", "general_coordinator"))
    if mode not in {"deep_research", "general_coordinator"}:
        log(LOGGER, "WARN", "Router returned an unknown mode; using coordinator", mode=mode)
        mode = "general_coordinator"
    reason = str(value.get("reason", ""))[:500]
    log(LOGGER, "INFO", "Routing decision completed", mode=mode, reason=reason)
    return {
        "mode": mode,
        "reason": reason,
        "event": {
            "step_id": str(uuid4()),
            "agent_name": "gui_router",
            "type": "routing_decision",
            "decision": mode,
            "reason": reason,
            "tokens": token_usage(response),
            "timestamp": now(),
        },
    }


def _dispatch_query(
    api_key: str, model: str, session_id: str, query: str
) -> dict:
    route = _route_query(api_key, model, query)
    if route["mode"] == "deep_research":
        log(LOGGER, "INFO", "Starting deep-research workflow", session_id=session_id)
        result = _public_run(start_deep_research(api_key, model, query))
        result["route_reason"] = route["reason"]
        result["events"] = [route["event"], *result["events"]]
        return result
    log(LOGGER, "INFO", "Starting general coordinator", session_id=session_id)
    result = _public_turn(
        run_turn(api_key, model, session_id, query), session_id, route
    )
    result["events"] = [route["event"], *result["events"]]
    return result


def _start_operation(target, run_id: str | None = None) -> str:
    operation_id = str(uuid4())
    with LOCK:
        if run_id and run_id in ACTIVE_RUNS:
            raise ValueError("This research run already has an active operation.")
        if run_id:
            ACTIVE_RUNS.add(run_id)
        OPERATIONS[operation_id] = {"status": "running", "result": None, "error": ""}
    log(LOGGER, "DEBUG", "Background operation queued", operation_id=operation_id)

    def execute() -> None:
        try:
            log(LOGGER, "DEBUG", "Background operation started", operation_id=operation_id)
            result = target()
            with LOCK:
                OPERATIONS[operation_id] = {
                    "status": "completed",
                    "result": result,
                    "error": "",
                }
            log(LOGGER, "INFO", "Background operation completed", operation_id=operation_id)
        except Exception as error:
            with LOCK:
                OPERATIONS[operation_id] = {
                    "status": "failed",
                    "result": None,
                    "error": str(error),
                }
            log(
                LOGGER,
                "ERROR",
                "Background operation failed",
                operation_id=operation_id,
                error=str(error),
            )
        finally:
            if run_id:
                with LOCK:
                    ACTIVE_RUNS.discard(run_id)

    threading.Thread(target=execute, daemon=True).start()
    return operation_id


class AgentGuiHandler(BaseHTTPRequestHandler):
    server_version = "MultiAgentGUI/1.0"

    def log_message(self, format_string: str, *args) -> None:
        rendered = format_string % args
        if "/api/logs" in rendered:
            return
        log(
            LOGGER,
            "DEBUG",
            "HTTP request",
            client=self.address_string(),
            request=rendered,
        )

    def _json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _request_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 32_000:
            raise ValueError("Request body must be between 1 byte and 32 KB.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/logs":
                raw_after = parse_qs(parsed.query).get("after", ["0"])[0]
                after = max(0, int(raw_after))
                self._json(200, logs_after(after))
                return
            if path == "/api/evals/dataset":
                from evals.run import load_examples

                examples = load_examples(None, None)
                self._json(200, {"examples": examples, "count": len(examples)})
                return
            if path.startswith("/api/operations/"):
                operation_id = path.rsplit("/", 1)[-1]
                with LOCK:
                    operation = OPERATIONS.get(operation_id)
                if operation is None:
                    self._json(404, {"error": "Unknown operation."})
                else:
                    self._json(200, operation)
                return
            if path.startswith("/api/research/"):
                run_id = path.rsplit("/", 1)[-1]
                if not RUN_ID.fullmatch(run_id):
                    raise ValueError("Invalid research run ID.")
                self._json(200, _public_run(resume_deep_research(run_id)))
                return
            self._json(404, {"error": "Not found."})
        except Exception as error:
            self._json(400, {"error": str(error)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._request_json()
            api_key = os.environ.get("GEMINI_API_KEY", "")
            model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
            if not api_key:
                raise ValueError("Set GEMINI_API_KEY in .env first.")
            if path == "/api/query":
                query = str(payload.get("query", "")).strip()[:8_000]
                if not query:
                    raise ValueError("A query is required.")
                session_id = str(payload.get("session_id", ""))
                if not RUN_ID.fullmatch(session_id):
                    raise ValueError("Invalid session ID.")
                operation_id = _start_operation(
                    lambda: _dispatch_query(api_key, model, session_id, query),
                    session_id,
                )
                self._json(202, {"operation_id": operation_id})
                return
            if path == "/api/evals/run":
                from evals.run import run_evaluation

                raw_ids = payload.get("ids", [])
                if not isinstance(raw_ids, list):
                    raise ValueError("ids must be an array.")
                selected_ids = {str(item) for item in raw_ids} or None
                raw_limit = payload.get("limit")
                limit = None if raw_limit in {None, ""} else max(1, min(int(raw_limit), 100))
                judge = bool(payload.get("judge", False))
                log(LOGGER, "INFO", "Phoenix evaluation requested",
                    selected_count=len(selected_ids or []), limit=limit, judge=judge)
                operation_id = _start_operation(
                    lambda: run_evaluation(selected_ids, limit, judge)
                )
                self._json(202, {"operation_id": operation_id})
                return
            if path == "/api/evals/dataset":
                from evals.run import append_example

                example = append_example(payload)
                log(LOGGER, "INFO", "Evaluation example created", example_id=example["id"])
                self._json(201, {"example": example})
                return
            if path == "/api/research/select":
                run_id = str(payload.get("run_id", ""))
                if not RUN_ID.fullmatch(run_id):
                    raise ValueError("Invalid research run ID.")
                option_number = int(payload.get("option_number", 0))
                feedback = str(payload.get("feedback", ""))[:2_000]
                operation_id = _start_operation(
                    lambda: _public_run(
                        select_research_direction(
                            api_key,
                            model,
                            resume_deep_research(run_id),
                            option_number,
                            feedback,
                        )
                    ),
                    run_id,
                )
                self._json(202, {"operation_id": operation_id})
                return
            self._json(404, {"error": "Not found."})
        except Exception as error:
            self._json(400, {"error": str(error)})


def serve_gui(host: str = "127.0.0.1", port: int = 9999) -> None:
    """Serve the local development UI until interrupted."""
    try:
        server = ThreadingHTTPServer((host, port), AgentGuiHandler)
    except OSError as error:
        log(LOGGER, "FATAL", "Unable to start Multi-Agent Console",
            host=host, port=port, error=str(error))
        raise
    log(LOGGER, "INFO", "Multi-Agent Console started", url=f"http://localhost:{port}")
    log(LOGGER, "INFO", "Terminal mode remains available", command="python main.py --cli")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log(LOGGER, "WARN", "Stopping Multi-Agent Console")
    finally:
        server.server_close()
