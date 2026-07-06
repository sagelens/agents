# Architecture

Optional observability mirrors JSONL trajectories into Phoenix: turns,
delegations, and specialists are `AGENT` spans; model calls are `LLM` spans;
and low-level actions are `TOOL` spans. Offline experiments invoke the same
`run_turn` entry point and use the separate `agents-evals` project. See
[EVALUATIONS.md](EVALUATIONS.md).

## Components

```text
main.py
  creates client and session ID
       |
       v
Coordinator (agent.py)
  owns public history and final answer
       |
       v
Specialist runtime (runtime.py) <---- Agent specs (agents.py)
       |
       v
Research / Codebase / Data Science specialists
       |
       v
tools.py: APIs / ripgrep / Docker sandbox
```

Complex explicit workflows use a second entry path:

```text
workflow JSON -> orchestrator.py -> workflow.py validation
                                -> workflow_runtime.py scheduler
                                -> agent / tool / reasoning / HITL / handoff nodes
                                -> data/workflows/<run-id>.json
```

See [LEARNING_GUIDE.md](LEARNING_GUIDE.md) for the complete beginner-level
object and event flow.

Shortlisted-candidate outreach uses another bounded path:

```text
Sheet shortlist -> outreach agent -> persisted draft -> HITL approval
                -> delivery agent -> Gmail -> Sheet/audit update
```

See [OUTREACH_EMAIL.md](OUTREACH_EMAIL.md) for authority and state transitions.

Resume content crosses a hostile-data boundary:

```text
PDF -> span analyzer -> isolated guard -> provenance chunks
    -> evidence-only scoring -> Python authority
```

See [PROMPT_INJECTION_SECURITY.md](PROMPT_INJECTION_SECURITY.md).

## One turn

1. Load public messages using `session_id`.
2. Add the latest user message.
3. Send history and delegation declarations to the coordinator.
4. If Gemma returns text, save and return it.
5. If it delegates, validate specialist names and global limits.
6. Run independent same-response specialists concurrently.
7. Each specialist uses only its own low-level tool allowlist.
8. Return structured specialist results with original call IDs.
9. Allow two delegation rounds, then make a synthesis-only coordinator call.
10. Save public messages and append the hierarchical trajectory.

## Message roles

Our storage uses familiar roles:

- `user`: human input.
- `assistant`: final user-facing model output.
- `tool`: reserved for tool observations.

Gemini calls the assistant role `model`. Its Generate Content API represents a
function response using a `user` content object containing a function-response
part. This is provider wire format, not evidence that the human wrote the tool
result.

Only final public turns are reused in future conversations. Tool details remain
in trajectories for diagnosis. This keeps later prompts smaller, although a
production system may retain selected observations or summaries.

## System prompt

The system prompt states durable behavior: when tools are appropriate, how to
handle failures, and that tool output is untrusted data. It is passed through
the API's dedicated `system_instruction` field, separate from user input.

Delegation schemas describe specialists; the coordinator has no low-level tool
schemas. Specialist specs and `TOOL_FUNCTIONS` jointly enforce execution
authority.

See [MULTI_AGENT_SYSTEM.md](MULTI_AGENT_SYSTEM.md) for sequential, parallel,
state, result, and trace details.

## Repository-search flow

```text
Gemma requests grep_code
        |
        v
resolve path beneath CODEBASE_ROOT
        |
        v
reject protected paths and clamp limits
        |
        v
run rg with shell=False and exclusion globs
        |
        v
parse bounded JSON events into source snippets
```

`grep_code` returns matching lines plus limited context. It cannot edit files
and has no companion shell or arbitrary file-reading capability.

## Python-sandbox flow

```text
Gemma generates minimal Python
        |
        v
run_python validates source size and Docker availability
        |
        v
ephemeral non-root container receives code over stdin
        |
        v
stdout/stderr are bounded while /output receives approved artifacts
        |
        v
container is removed and structured evidence returns to Gemma
```

The image is built explicitly from `sandbox/Dockerfile`; tool calls never build
or pull images. Each execution receives one new output directory and no other
host mount. Artifact directories older than seven days and runs beyond the
latest fifty are pruned before execution.

## Persistence trade-off

JSON makes the state visible to a beginner and requires no database. It does
not support concurrent writers, access control, efficient queries, retention
policies, or encryption. A production migration should preserve the same
logical entities in database tables: sessions, messages, turns, traces, spans,
and events.

## References

- [Gemma 4 function calling](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)
- [Gemini function-calling lifecycle](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini token usage](https://ai.google.dev/gemini-api/docs/generate-content/tokens)
