# Architecture

## Components

```text
main.py
  creates client and session ID
       |
       v
agent.py <---- prompt.py
  loads history, calls model, dispatches tools, builds trace
       |                         |
       v                         v
store.py                     tools.py
  JSON history/JSONL trace     Open-Meteo/DuckDuckGo/ripgrep
```

## One turn

1. Load public messages using `session_id`.
2. Add the latest user message.
3. Send history, system instruction, and tool declarations to Gemma.
4. If Gemma returns text, save and return it.
5. If Gemma returns function calls, validate names against the allowlist.
6. Execute tools and measure each latency.
7. Return function responses with their original call IDs.
8. Allow at most two tool rounds, then make a synthesis-only model call.
9. Save messages and append the completed trajectory.

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

Tool schemas describe capabilities and arguments. They do not grant execution
authority; only `TOOL_FUNCTIONS` does.

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
