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

8. Run the agent: `python main.py`

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
