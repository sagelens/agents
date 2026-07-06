# Observability and trajectories

When `PHOENIX_ENABLED=true`, `src/telemetry.py` mirrors the completed hierarchy
to local Phoenix using OpenInference `AGENT`, `LLM`, and `TOOL` spans. Explicit
parents preserve parallel fan-out. Export failures never fail the application,
and JSONL remains authoritative. See [EVALUATIONS.md](EVALUATIONS.md).

## Trace hierarchy

```text
session
  turn / trace
    coordinator model_call
      delegation_start
        specialist model_call
        specialist tool_call
      delegation_end
    coordinator synthesis
```

A session spans conversation turns. A trace covers one turn. Each operation is
a step analogous to an observability “span.”

Every event includes `agent_name`, `agent_run_id`, `depth`, and
`parent_step_id`. These fields represent sequential delegations as a path and
parallel delegations as a fan-out/fan-in DAG.

## Model event fields

- `step_id`: unique operation ID.
- `type`: `model_call`.
- `number`: loop position.
- `decision`: `call_tool` or `final_answer`.
- `tool_names`: requested functions, if any.
- `latency_ms`: provider round-trip time.
- `tokens`: provider-reported input, output, thinking, and total counts.
- `timestamp`: UTC completion time.

## Tool event fields

- `tool`: allowlisted function name.
- `arguments`: validated model-supplied values.
- `result`: observation or structured error.
- `latency_ms`: complete local function duration, including HTTP calls.

Tool results and arguments can contain sensitive information. Production logs
need redaction, access controls, encryption, and retention limits.

For `grep_code`, arguments record the pattern, relative path, glob, and limits.
The result records repository-relative file paths, source lines, match count,
and whether output was truncated. Its latency includes starting ripgrep,
searching files, parsing events, and stopping at the configured limit.

For `run_python`, the tool event records generated source, status, exit code,
bounded stdout and stderr, sandbox duration, timeout or limit state, and
artifact metadata. The existing tool-call latency also includes Docker
preflight, startup, cleanup, and artifact inspection.

The trajectory also contains per-specialist summaries and whole-turn token
totals. Specialist latency is measured independently. When specialists run in
parallel, their summed latency may exceed the turn wall-clock latency.

## Why chain-of-thought is absent

Operational debugging usually needs reproducible external evidence: prompts,
tool selection, arguments, observations, errors, tokens, and timings. Private
chain-of-thought is neither necessary nor an appropriate telemetry contract.
The trace therefore records the decision category, not hidden reasoning text.

## JSON Lines

`trajectories.jsonl` stores one JSON object per line. Appending is simple, and
analytics tools can stream records without loading the entire file. In a
production service, emit equivalent spans through OpenTelemetry.

Relevant standards:

- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
