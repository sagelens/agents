# Python ReAct Agent

This is a deliberately small, framework-free AI agent. It uses Google's hosted
`gemma-4-31b-it` model and exposes exactly two application tools:
`get_weather`, `web_search`, and the read-only `grep_code`.

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

Start with [docs/CONCEPTS.md](docs/CONCEPTS.md), then read `main.py`,
`agent.py`, `tools.py`, and `store.py` in that order.

## Setup

1. Revoke the API key that was shared in chat and add its replacement to `.env`.
2. Enter this directory: `cd /Users/apurv_/Downloads/vibes/agents`
3. Create an environment: `python3 -m venv .venv`
4. Activate it: `source .venv/bin/activate`
5. Install packages: `pip install -r requirements.txt`
6. Run the agent: `python main.py`

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
```

## Generated files

- `data/sessions/<session-id>.json`: public multi-turn conversation history.
- `data/trajectories.jsonl`: one complete observable trajectory per line.

These files may contain user questions and tool results. Do not publish them
without reviewing their contents.

## Deliberate first-version limits

- Terminal interface only.
- Sequential tool execution.
- Two tool-planning rounds followed by one mandatory synthesis call.
- Local JSON storage, suitable for learning rather than concurrent production.
- No hidden reasoning is logged.
- Code search cannot leave `CODEBASE_ROOT` or inspect common secret files.
- No tests are included, following the project instruction.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the flow and
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) for every recorded field.
