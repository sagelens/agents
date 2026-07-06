# Python ReAct Agent

This is a deliberately understandable, framework-free multi-agent system using
Google's hosted `gemma-4-31b-it` model.

The user-facing coordinator delegates to three stateless specialists:

- Research Agent: `get_weather` and `web_search`
- Codebase Agent: read-only `grep_code`
- Data Science Agent: Docker-sandboxed `run_python`

```text
User
   │
   ▼
Runner
   │
   ▼
Agent
   ├── Prompt / Instructions
   ├── Tools
   ├── Memory
   ├── State and trajectories
   └── Model (Gemma through the Gemini API)
```

Start with [docs/MULTI_AGENT_SYSTEM.md](docs/MULTI_AGENT_SYSTEM.md), then read
`main.py`, `src/agent.py`, `src/agents.py`, `src/runtime.py`, and
`src/tools.py` in that order.

For visual traces and offline agent evaluations, read
[docs/EVALUATIONS.md](docs/EVALUATIONS.md). For the low-level call path from
dataset loading through Phoenix REST persistence, read
[docs/EVAL_INTERNALS.md](docs/EVAL_INTERNALS.md).

## Setup

1. Revoke the API key that was shared in chat and add its replacement to `.env`.
2. Enter this directory: `cd /Users/apurv_/Downloads/vibes/agents`
3. Create an environment: `python3 -m venv .venv`
4. Activate it: `source .venv/bin/activate`
5. Install packages: `pip install -r requirements.txt`
6. Start Docker Desktop.
7. Build the local sandbox image:

   ```bash
   docker build -t agents-python-sandbox:1 sandbox
   ```

8. Run the local GUI: `python main.py`
9. Open `http://localhost:9999`.

The GUI accepts general questions and dynamically routes them through the
coordinator's research, codebase, or data-science specialists. Queries needing
iterative investigation enter the deep-research HITL flow. Debug Mode displays
routing, coordinator, model, tool, compaction, and lifecycle events. The server
binds to `127.0.0.1` and is intended for local development.

The interface uses a collapsible trace inspector on the left and a live,
color-coded log stream on the right. Structured runtime logs use `DEBUG`,
`INFO`, `WARN`, `ERROR`, and `FATAL` levels and are also printed to the
terminal.

Deep-research direction generation, memory updates, specialist calls, and
final synthesis start on `GEMINI_MODEL` and switch to
`DEEP_RESEARCH_FALLBACK_MODEL` (default `gemini-2.5-flash`) after two provider
failures. The fallback does not affect other workflows.

To use the original terminal interface instead:

```bash
python main.py --cli
```

Optional Phoenix quickstart:

```bash
docker compose -f compose.phoenix.yaml up -d
# Set PHOENIX_ENABLED=true in .env.
python main.py
open http://localhost:6006
python -m evals.run --limit 2
```

The grep tool requires ripgrep:

```bash
brew install ripgrep
```

Set `CODEBASE_ROOT` in `.env` to the only repository the agent may inspect.
Restart the agent after changing that value.

Example questions:

```text
Where is session history saved in this codebase?
Find every reference to TOOL_FUNCTIONS and explain how it works.
Which function enforces the agent's maximum number of model calls?
Generate a sample line chart with five points.
Use NumPy to calculate the mean and standard deviation of 2, 4, 8, 16.
```

## Generated files

- `data/sessions/<session-id>.json`: public multi-turn conversation history.
- `data/trajectories.jsonl`: one complete observable trajectory per line.
- `artifacts/<run-id>/`: approved PNG, CSV, JSON, and text outputs from Python.

These files may contain user questions and tool results. Do not publish them
without reviewing their contents.

## Deliberate first-version limits

- Terminal interface only.
- Sequential tool execution.
- Independent specialists may run concurrently; each specialist's tools remain sequential.
- Two coordinator delegation rounds followed by one mandatory synthesis call.
- At most one invocation per specialist per turn and no recursive delegation.
- Local JSON storage, suitable for learning rather than concurrent production.
- No hidden reasoning is logged.
- Only the coordinator owns public multi-turn memory.
- Code search cannot leave `CODEBASE_ROOT` or inspect common secret files.
- Generated Python runs without network access, host credentials, or repository access.
- Python execution is limited to 15 seconds, 512 MB RAM, one CPU, and 64 processes.
- No tests are included, following the project instruction.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the flow and
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) for every recorded field.

## DAG workflows, reasoning, HITL, and handoffs

The project also includes a framework-free DAG runtime with agent, tool,
reasoning-chain, tree-search, human-approval, handoff, and join nodes.

Run the documented example from the interactive terminal:

```text
/workflow examples/learning_workflow.json
```

Start with [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md). It follows the
low-level execution from JSON parsing through validation, scheduling,
specialist calls, agent messages, approvals, persistence, and final results.

## Iterative deep research

Start a resumable, human-directed web research session:

```text
/research How will agent interoperability affect enterprise software?
```

At each pause, select one of four research directions and optionally add
guidance. After at least one research round, one direction finishes and
synthesizes the cited report. Leave the prompt with `exit` and continue later:

```text
/research-resume <run-id>
```

Runs are stored in `data/research/`. Stateless research specialists receive
only the selected focus and compact research memory; raw interaction history
does not enter the coordinator prompt. Active context is compacted when
`DEEP_RESEARCH_COMPACTION_TOKENS` is reached.

For a complete beginner-friendly walkthrough of the code, state machine,
Tree-of-Thought branching, HITL handoffs, source provenance, and dynamic
compaction, read [docs/DEEP_RESEARCH_AGENT.md](docs/DEEP_RESEARCH_AGENT.md).

For a line-by-line explanation of a real multi-agent weather-to-chart trace,
including routing, dependent delegation, retries, tools, sandbox execution,
tokens, persistence, and parent/child event IDs, read
[docs/TRACE_EXECUTION_WALKTHROUGH.md](docs/TRACE_EXECUTION_WALKTHROUGH.md).

For a real deep-research trace covering HITL Tree-of-Thought choices,
Gemma-to-Flash fallback, source merging, recursive memory updates, and a
detailed explanation of dynamic compaction in agent harnesses, read
[docs/DEEP_RESEARCH_TRACE_AND_COMPACTION.md](docs/DEEP_RESEARCH_TRACE_AND_COMPACTION.md).

## Resume screening

After sharing the configured Drive folder and spreadsheet with the service
account, run:

```text
/screen-resumes
```

This screens direct PDF children for the predefined Full-Stack AI Engineer role
and records every outcome in Google Sheets. See
[docs/RESUME_SCREENING.md](docs/RESUME_SCREENING.md) for setup, scoring,
privacy, repeat-run behavior, and recovery.

## HITL outreach

Authorize a Gmail sender, inspect status, and review each shortlisted
candidate's invitation before sending:

```text
/authorize-email
/email-auth-status
/outreach-status
/outreach-shortlisted
```

See [docs/OUTREACH_EMAIL.md](docs/OUTREACH_EMAIL.md) for OAuth setup, agent
authority boundaries, draft hashing, approval/edit/reject behavior, Gmail
delivery, duplicate protection, commands, and recovery.

For the complete PDF → scoring → Sheet → outreach → HITL → Gmail lifecycle,
including the exact behavior of approvals left pending for 10–15 days, read
[docs/END_TO_END_HIRING_WORKFLOW.md](docs/END_TO_END_HIRING_WORKFLOW.md).

For a grounded comparison of Google ADK collaboration modes, handoffs,
Agent2Agent communication, HITL confirmation, and resumability against this
framework-free implementation, see
[docs/GOOGLE_ADK_COMPARISON.md](docs/GOOGLE_ADK_COMPARISON.md).

The resume prompt-injection threat model, layered controls, and manual review
workflow are documented in
[docs/PROMPT_INJECTION_SECURITY.md](docs/PROMPT_INJECTION_SECURITY.md).
