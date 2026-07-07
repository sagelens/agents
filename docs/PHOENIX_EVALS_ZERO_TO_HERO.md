# Phoenix Evaluations: From Zero to This Codebase

## 1. Purpose of this guide

This guide assumes no prior knowledge of Phoenix, OpenTelemetry, traces,
datasets, experiments, or agent evaluations. It explains:

- what Phoenix is and is not;
- why agent systems need traces and evaluations;
- how the Phoenix server, client, collector, and UI work together;
- every important evaluation concept;
- how this repository creates traces;
- how its JSONL dataset becomes a Phoenix dataset;
- how the production agent is executed for each example;
- how deterministic and LLM evaluators produce scores;
- how the CLI and local UI launch experiments;
- how to interpret results;
- how to add examples safely;
- setup, troubleshooting, privacy, cost, and production limitations.

The most important distinction is:

> A trace records what happened. An evaluation measures whether what happened
> was good.

## 2. Why ordinary application logs are not enough

A traditional application might log:

```text
request received
database queried
response returned
```

An agent request may contain:

```text
user request
-> router model decision
-> coordinator model decision
-> specialist delegation
-> specialist model decision
-> tool call
-> tool result
-> another model decision
-> another specialist
-> final synthesis
```

A final answer can look correct even when:

- the wrong specialist was selected;
- an unnecessary agent ran;
- a forbidden tool was attempted;
- a tool failed and the model hid the failure;
- the model invented a source;
- the route used too many calls;
- latency or token cost was excessive;
- an artifact was missing;
- parallel work accidentally ran sequentially.

Agent observability must therefore capture the complete execution trajectory,
not only the final string.

## 3. What Phoenix is

Phoenix is an observability and evaluation platform for AI applications. In
this project it serves two roles.

### Role A: flight recorder

Phoenix receives traces containing nested spans for:

- complete user turns;
- coordinator model calls;
- delegations;
- specialist executions;
- specialist model calls;
- tool calls;
- provider errors.

The UI displays their hierarchy, duration, status, token usage, inputs, and
outputs.

### Role B: report card

Phoenix stores a versioned dataset, runs the application against each example,
stores the generated output, invokes evaluators, and displays scores.

Phoenix does **not**:

- replace Gemini or Gemma;
- decide which agent should run;
- execute this repository's tools by itself;
- define what “correct” means;
- automatically make production safe;
- modify the agent after finding a bad result.

Our Python code supplies the application task, expectations, and evaluators.
Phoenix orchestrates experiment execution and stores and visualizes results.

## 4. Phoenix components

```text
                       Phoenix server
                  +----------------------+
                  | REST API             |
Python client --->| Dataset storage      |
                  | Experiment storage   |
                  | Evaluation scores    |
                  | Web UI               |
                  +----------------------+
                           ^
                           |
                    OTLP trace collector
                           ^
                           |
                    OpenTelemetry spans
                           |
                    Multi-agent runtime
```

### Phoenix server

The long-running service that stores datasets, experiments, annotations, and
traces and renders the browser UI.

### Phoenix Python client

The library used by `evals/run.py`. It uploads datasets and coordinates
experiments by invoking Python task and evaluator callables.

### OTLP collector

An endpoint that accepts OpenTelemetry Protocol trace data. This project sends
HTTP/protobuf spans to:

```text
http://localhost:6006/v1/traces
```

### Phoenix UI

The browser application at:

```text
http://localhost:6006
```

### Phoenix Evals

Optional evaluator utilities, including an LLM adapter used by this project
for LLM-as-judge scoring.

## 5. Core vocabulary

### Trace

One complete end-to-end operation. Here, one normal user turn is one trace.

### Span

One timed operation inside a trace. Spans can be nested.

```text
turn span
├── coordinator model span
├── delegation span
│   └── specialist span
│       ├── specialist model span
│       └── tool span
└── final coordinator model span
```

### Attribute

Metadata attached to a span, such as model name, token count, tool name,
status, session ID, or bounded input/output.

### Dataset

A named, versioned collection of examples.

### Example

One input and its expected behavior.

### Experiment

One execution of an application version against a dataset version.

### Task

The Python callable that receives one dataset input and returns the
application's observed output.

### Evaluator

A Python callable or model that compares observed output with expected output
and returns scores.

### Score

A numeric measurement, frequently zero or one.

### Label

A readable result such as `completed`.

### Annotation

A score, label, or explanation attached to an experiment run.

### LLM-as-judge

A model that grades output using a rubric. It is flexible but probabilistic and
can be biased.

## 6. Trace versus evaluation

Consider:

```text
What is the current weather in Delhi?
```

The expected execution is:

```text
coordinator
-> research_agent
-> get_weather
-> coordinator synthesis
```

A trace answers:

- Which calls happened?
- How were they nested?
- How long did each call take?
- Which model and tool were used?
- Did a provider retry occur?
- How many tokens were consumed?

An evaluation answers:

- Was `research_agent` the correct specialist?
- Was the route `single`?
- Did the tool stay within the agent's allowlist?
- Did the request complete?
- Did the path avoid redundant delegations?
- Was latency acceptable?

The evaluator derives measurements from trace-backed structured output.

## 7. How Phoenix runs locally

`compose.phoenix.yaml` starts two containers.

### Volume initializer

```yaml
phoenix-volume-init:
  image: busybox:1.36.1
```

It changes ownership of the named volume to Phoenix's non-root UID `65532`.

### Phoenix server

```yaml
phoenix:
  image: arizephoenix/phoenix:version-17.5.0-nonroot
```

The image is pinned so behavior does not change silently.

### Ports

```text
127.0.0.1:6006 -> UI, REST API, OTLP HTTP
127.0.0.1:4317 -> OTLP gRPC
```

Binding to `127.0.0.1` prevents network exposure by default.

### Storage

The `phoenix_data` Docker volume contains durable Phoenix state. Normal
container restarts preserve it.

### Privacy-related configuration

```yaml
PHOENIX_TELEMETRY_ENABLED: "false"
PHOENIX_ALLOW_EXTERNAL_RESOURCES: "false"
```

Phoenix product telemetry and external UI resources are disabled.

## 8. Starting Phoenix

From the project root:

```bash
docker compose -f compose.phoenix.yaml up -d
```

Check status:

```bash
docker compose -f compose.phoenix.yaml ps
```

Open the UI:

```bash
open http://localhost:6006
```

View logs:

```bash
docker compose -f compose.phoenix.yaml logs phoenix
```

Stop while retaining data:

```bash
docker compose -f compose.phoenix.yaml down
```

Delete all Phoenix data only intentionally:

```bash
docker compose -f compose.phoenix.yaml down -v
```

## 9. Environment variables

### `PHOENIX_ENABLED`

Controls application trace export. `src/telemetry.py` is a no-op unless this is
truthy.

### `PHOENIX_COLLECTOR_ENDPOINT`

OTLP HTTP endpoint:

```text
http://localhost:6006/v1/traces
```

### `PHOENIX_BASE_URL`

Base URL used by the Phoenix Python client for datasets and experiments:

```text
http://localhost:6006
```

### `PHOENIX_PROJECT_NAME`

Interactive application traces:

```text
agents-dev
```

### `PHOENIX_EVAL_PROJECT`

Traces generated during offline evaluation:

```text
agents-evals
```

### `PHOENIX_EVAL_MODEL`

Model used only when the optional LLM judge is enabled.

## 10. Why there are two Phoenix projects

Interactive queries and deliberate evaluation traffic have different
purposes.

### `agents-dev`

Contains traces generated while using the local agent normally.

### `agents-evals`

Contains traces generated by dataset experiments.

Separation prevents evaluation traffic from distorting normal latency, error,
and routing analysis.

## 11. Application trace source of truth

Phoenix is not the only record. `finish_turn` creates an application
trajectory and appends it to:

```text
data/trajectories.jsonl
```

The local trajectory is authoritative. Phoenix is an optional visual copy.

If Phoenix is unavailable:

- the agent still returns an answer;
- JSONL persistence still works;
- `phoenix_trace_id` is `None`;
- exporter failure is swallowed.

This is an intentional reliability boundary: observability must not take down
the application.

## 12. Telemetry setup

`configure_telemetry` in `src/telemetry.py`:

1. checks `PHOENIX_ENABLED`;
2. lazily imports `phoenix.otel.register`;
3. reads collector endpoint and project name;
4. registers HTTP/protobuf OTLP export;
5. disables batch mode for immediate local export;
6. disables automatic instrumentation;
7. creates a manual tracer.

Important options:

```python
batch=False
auto_instrument=False
set_global_tracer_provider=False
```

The project manually translates known trajectory events instead of
automatically capturing every SDK call.

## 13. Why manual instrumentation is used

Manual instrumentation provides:

- stable domain-specific span names;
- explicit parent-child hierarchy;
- bounded attributes;
- control over exported content;
- no dependency on framework internals;
- easy matching with JSONL events.

The cost is more code and maintenance. New event types must be added
explicitly.

## 14. The exported span tree

`export_trajectory` creates:

```text
multi_agent_turn                    AGENT
├── coordinator.model.1             LLM
├── delegate.research_agent         AGENT
│   └── research_agent              AGENT
│       ├── research_agent.model.1  LLM
│       └── tool.get_weather        TOOL
└── coordinator.model.2             LLM
```

Parallel agents appear as sibling delegation branches. Dependent agents appear
after separate coordinator calls.

## 15. Root turn span

The root span includes:

- bounded user input;
- bounded final output;
- session ID;
- turn ID;
- application trace ID;
- coordinator identity;
- status;
- total tokens;
- measured start and end timestamps.

Phoenix generates its own OpenTelemetry trace ID. The application stores that
ID as `phoenix_trace_id` for correlation.

## 16. Coordinator model spans

Every depth-zero `model_call` event becomes an LLM span containing:

- configured model;
- decision: delegate or final answer;
- requested delegation tools;
- prompt tokens;
- completion tokens;
- total tokens;
- measured latency.

## 17. Delegation and specialist spans

A `delegation_start` and matching `delegation_end` define the delegation
duration. A specialist agent span is nested below it.

The specialist span contains:

- target agent name;
- specialist run ID;
- depth;
- bounded structured output;
- completion status.

## 18. Specialist model and tool spans

Depth-one events become:

- LLM spans for model calls and model errors;
- TOOL spans for tool calls.

Tool spans contain bounded validated arguments and results. Provider errors
are marked with OpenTelemetry error status.

## 19. Time reconstruction

Events store completion timestamps and measured latency. `_start_ns` subtracts
latency from completion time to infer span start.

This preserves parallel overlap in the Phoenix timeline.

## 20. Attribute bounding

`_content` truncates exported text to a configured limit. This protects the
collector and UI from unbounded tool output.

Bounding is not full privacy redaction. Sensitive input should not be collected
unless required and authorized.

## 21. Export shutdown

`shutdown_telemetry` calls `force_flush` best-effort. This matters for short
evaluation and CLI processes that may exit before buffered spans are sent.

## 22. Evaluation dataset source

The human-readable source is:

```text
evals/dataset.jsonl
```

It currently contains 20 examples covering:

- direct answers;
- live weather;
- web research;
- code search;
- calculations;
- chart artifacts;
- parallel routes;
- sequential dependencies;
- boundary bypass attempts;
- prompt injection resistance;
- robustness and edge cases.

Each line is an independent JSON object.

## 23. Dataset example schema

```json
{
  "id": "weather",
  "prompt": "What is the current weather in Delhi?",
  "expected": {
    "agents": ["research_agent"],
    "mode": "single",
    "tools": ["get_weather"],
    "reference": "Report current Delhi weather using the weather tool."
  },
  "metadata": {
    "category": "grounding",
    "difficulty": "medium"
  }
}
```

### `id`

Stable unique identity used for filtering and comparison.

### `prompt`

The real input sent to the production agent.

### `expected.agents`

Ordered specialist route expected from the coordinator.

### `expected.mode`

One of:

- `direct`;
- `single`;
- `parallel`;
- `sequential`.

### `expected.tools`

Expected low-level tool sequence.

### `expected.reference`

Human-readable correct behavior used for inspection and the optional judge.

### `artifact_media_type`

Optional required artifact type, such as `image/png`.

### `metadata`

Optional category and difficulty information for dataset organization.

## 24. Loading examples

`load_examples`:

1. reads every non-empty JSONL line;
2. parses JSON;
3. optionally filters stable IDs;
4. optionally applies a limit;
5. rejects unknown requested IDs.

Validation occurs before model execution, preventing misleading empty runs.

## 25. UI-created examples

`append_example` validates:

- required fields;
- stable ID format;
- unique ID;
- non-empty prompt;
- object-shaped expectations;
- valid mode;
- list-shaped agents and tools.

It rewrites the JSONL file through a temporary file and atomic replace.

The Dataset Builder collects:

- ID;
- prompt;
- expected mode;
- comma-separated expected agents;
- comma-separated expected tools;
- reference behavior;
- category;
- difficulty.

## 26. Phoenix dataset conversion

Readable JSONL examples become Phoenix envelopes:

```python
{
    "id": example["id"],
    "input": {"prompt": example["prompt"]},
    "output": example["expected"],
    "metadata": {"suite": DATASET_NAME},
}
```

The naming is easy to confuse:

```text
Phoenix dataset input  -> task input
Phoenix dataset output -> expected/reference value
task return value      -> observed output
```

## 27. Dataset name and versions

The name is:

```text
framework-free-multi-agent-v1
```

Repeated uploads create comparable dataset versions. Stable IDs associate the
same logical examples across versions.

An experiment always targets one concrete dataset version.

## 28. What an experiment does

`client.experiments.run_experiment`:

1. creates an experiment record;
2. reads examples from the uploaded dataset version;
3. invokes the task for each example;
4. stores task outputs as experiment runs;
5. invokes every evaluator;
6. stores evaluator annotations;
7. prints a summary;
8. returns control to the harness.

The project sets:

```text
retries=0
timeout=120
```

Application-level model retries still exist. Phoenix does not rerun an entire
failed example.

## 29. Experiment names

Names use UTC timestamps:

```text
agents-eval-YYYYMMDD-HHMMSS
```

This makes repeated runs distinguishable and sortable.

## 30. The real task closure

`make_task` returns:

```python
def task(input):
    result = run_turn(api_key, model, str(uuid4()), input["prompt"])
    return summarize_result(result)
```

Every example gets a fresh session UUID. Examples therefore cannot contaminate
one another through conversation memory.

## 31. Why using the production entry point matters

The experiment exercises the real:

- coordinator prompt;
- specialist prompts;
- model API;
- function calling;
- tool validation;
- web and weather calls;
- code search;
- Docker sandbox;
- retry policy;
- token accounting;
- artifact collection;
- telemetry export.

It is not a mock or simplified evaluation agent.

This makes results realistic but means external availability and cost affect
the experiment.

## 32. Result normalization

`summarize_result` converts a large trajectory into stable fields:

```text
answer
agents
mode
tools
statuses
artifacts
tokens
latency_ms
status
trace_id
phoenix_trace_id
delegation_count
coordinator_calls
```

Evaluators consume these fields rather than parsing prose.

## 33. Route classification

`route_summary` inspects `delegation_start` events.

### Direct

No delegation starts.

### Single

Exactly one delegation.

### Parallel

Multiple delegation starts share the same parent coordinator step.

### Sequential

Delegations have different parent coordinator steps.

This uses actual execution evidence rather than trusting the final answer.

## 34. Deterministic evaluator

`deterministic_evaluator(output, expected)` always runs. It returns ten named
records.

### `specialist_selection`

```text
1 if actual ordered agents == expected agents
0 otherwise
```

### `execution_mode`

Compares direct/single/parallel/sequential mode.

### `tool_boundary`

Checks whether observed tools are a subset of tools allowed for the agents that
actually ran.

This is a security boundary check, not an exact expected-tool check.

### `loop_limits`

Checks delegation count and coordinator model calls against application
budgets.

### `completion`

One only when application status is `completed`. It also stores the readable
status label.

### `artifact_requirement`

If no artifact is required, it passes. If a media type is expected, at least
one artifact must match it.

### `path_efficiency`

Scores expected agent count relative to actual delegation count, bounded at
one.

### `redundant_delegations`

Counts repeated actual agent identities. Direction is `minimize`.

### `tokens`

Reports total model tokens. Direction is neutral; it is a measurement, not a
pass/fail threshold.

### `latency_ms`

Reports end-to-end latency. Also neutral.

## 35. Why deterministic evaluation comes first

Deterministic evaluators are:

- reproducible;
- inexpensive;
- easy to debug;
- suitable for policy and boundary checks;
- independent of judge-model preferences.

They cannot reliably grade nuanced answer quality or honesty, which motivates
the optional judge.

## 36. Optional LLM-as-judge

`make_judge` creates a Phoenix Evals `LLM` using:

```text
provider: google
model: PHOENIX_EVAL_MODEL
```

It asks for six binary dimensions:

- selection;
- delegation quality;
- tool handling;
- correctness;
- citation and failure honesty;
- conciseness.

## 37. Judge output

The judge must return JSON. The code removes an optional Markdown JSON fence,
parses the object, and emits scores prefixed with `judge_`.

Example:

```json
{
  "judge_correctness": 1,
  "judge_conciseness": 0
}
```

## 38. Why judge calls are suppressed from tracing

Without suppression, the model call that grades an experiment could appear as
another application trace and potentially confuse evaluation telemetry.

The harness temporarily sets OpenTelemetry's instrumentation-suppression
context around the judge call and detaches it afterward.

## 39. Risks of LLM judges

Judges can:

- prefer a writing style;
- disagree across runs;
- reward verbosity;
- miss factual errors;
- share biases with the application model;
- be vulnerable to text inside the answer;
- add cost and latency.

Calibrate judge scores against human labels. Never use an unvalidated judge as
the sole gate for high-risk HR behavior.

## 40. CLI execution path

Commands:

```bash
python -m evals.run
python -m evals.run --limit 2
python -m evals.run --ids chart,parallel_research_code
python -m evals.run --judge
```

`main` parses flags and calls `run_evaluation`.

## 41. Meaning of CLI flags

### No flags

Run all 20 examples.

### `--limit N`

Run only the first N examples after ID filtering.

### `--ids a,b`

Run selected stable IDs.

### `--judge`

Add the LLM judge to deterministic evaluation.

## 42. Local UI execution path

Start:

```bash
python main.py
```

Open:

```text
http://localhost:9999
```

Select **Evaluations**.

The browser:

1. loads `/api/evals/dataset`;
2. renders twenty checkboxes;
3. accepts optional limit and judge mode;
4. sends `POST /api/evals/run`;
5. receives a background-operation ID;
6. polls operation state;
7. displays experiment metadata and a Phoenix link.

## 43. Why UI evaluations run in the background

Real examples can make several model and tool calls and may take minutes.
Running in a daemon thread keeps:

- HTTP requests responsive;
- live logs streaming;
- operation status visible;
- other tabs usable.

The current in-memory operation registry is appropriate for local development,
not durable production jobs.

## 44. UI result fields

The UI displays:

- experiment name;
- dataset name;
- example count;
- selected IDs;
- application model;
- judge enabled/disabled;
- Phoenix URL.

Detailed rows and scores live in Phoenix.

## 45. Opening results in Phoenix

From the UI, select **Open experiment workspace in Phoenix**, or open:

```text
http://localhost:6006
```

Then:

1. open **Datasets & Experiments**;
2. choose `framework-free-multi-agent-v1`;
3. open the latest experiment;
4. inspect example rows and score columns;
5. open a row to compare input, expected output, and observed output;
6. use `phoenix_trace_id` to inspect execution.

## 46. Interpreting a row

Ask:

1. Did the task finish?
2. Was the route correct?
3. Did the correct tools execute?
4. Did tools stay within authority?
5. Were artifacts produced?
6. Were failures disclosed?
7. Was the path efficient?
8. What were token and latency costs?
9. Does the linked trace explain the score?

## 47. Common score patterns

### Correct answer, wrong route

Final wording looks fine, but `specialist_selection` is zero. The model may
have answered from memory instead of using current evidence.

### Correct agents, wrong mode

Agents are correct, but expected parallel work ran sequentially. Quality may be
fine while latency is unnecessarily high.

### Completion zero

Inspect provider errors, Docker availability, tool errors, and step limits.

### Artifact zero

The data-science agent may have failed to save under `/output`, Docker may be
unavailable, or returned artifacts may not match the required media type.

### High token count

Inspect repeated model rounds, oversized evidence, redundant delegations, and
long synthesis context.

## 48. Dataset design principles

A useful dataset should cover:

- common user paths;
- high-risk actions;
- agent boundaries;
- direct answers;
- each specialist;
- parallel and dependent work;
- tool failures;
- prompt injection;
- artifacts;
- ambiguous wording;
- edge cases.

Avoid a dataset composed only of happy-path demos.

## 49. Stable IDs

IDs should describe behavior rather than model version:

```text
parallel_weather_math
prompt_injection_code
sequential_web_analysis
```

Do not rename IDs casually; stable identity enables comparison.

## 50. Expected route ordering

`specialist_selection` compares ordered lists exactly. Parallel agent ordering
must therefore be defined consistently.

If multiple orders are equally valid, the evaluator should be extended to
accept a set of valid routes rather than encoding one arbitrary order.

## 51. Expected tools nuance

The current deterministic evaluator checks tool authority, not exact equality
with `expected.tools`. The expected list remains useful for inspection and the
judge.

A future evaluator could add:

```text
exact_tool_sequence
required_tools_present
forbidden_tools_absent
```

## 52. Dataset versioning

Adding examples creates a new local dataset content version. The next
experiment uploads a new Phoenix dataset version under the same name.

For team use, commit JSONL changes, review them like code, and include dataset
version identifiers in release records.

## 53. Experiment comparison

To compare a prompt or model change:

1. run a baseline experiment;
2. change one controlled variable;
3. run the same example IDs;
4. compare pass scores, route distribution, tokens, latency, and failures;
5. inspect changed traces.

Avoid changing model, prompts, tools, and dataset simultaneously.

## 54. Cost model

Experiment cost includes:

- router/coordinator model calls;
- specialist model calls;
- failed attempts and retries;
- optional judge calls;
- external APIs;
- Docker execution;
- trace and artifact storage.

`--limit` is useful while debugging.

## 55. Latency model

Experiment latency includes:

- model queue and generation time;
- tool network time;
- sequential dependencies;
- provider retries;
- sandbox startup;
- final synthesis;
- evaluator time.

Parallel branches overlap, so their durations should not simply be summed.

## 56. Failure isolation

Application telemetry failures are best-effort. Experiment infrastructure
failures are different: if Phoenix is unavailable, dataset upload or
experiment creation fails.

The local UI reports this through a failed background operation and live
`ERROR` log.

## 57. Troubleshooting: Phoenix UI does not open

Check:

```bash
docker compose -f compose.phoenix.yaml ps
docker compose -f compose.phoenix.yaml logs phoenix
lsof -nP -iTCP:6006 -sTCP:LISTEN
```

Common causes:

- Docker Desktop is stopped;
- port 6006 is occupied;
- volume ownership initialization failed;
- the image is unavailable.

## 58. Troubleshooting: no traces

Verify:

```dotenv
PHOENIX_ENABLED=true
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
```

Restart the Python process after changing `.env`.

Also verify the correct Phoenix project and remember that export failure does
not fail the user turn.

## 59. Troubleshooting: experiment fails immediately

Check:

- `GEMINI_API_KEY`;
- Phoenix base URL;
- selected IDs;
- JSONL syntax;
- Phoenix Python dependencies;
- model availability.

## 60. Troubleshooting: Docker examples fail

Build:

```bash
docker build -t agents-python-sandbox:1 sandbox
```

Check Docker Desktop and `SANDBOX_IMAGE`.

## 61. Troubleshooting: web examples vary

Current web content changes. Search engines may return different results or
fail transiently.

Keep deterministic route/tool checks separate from factual answer grading and
prefer official sources in reference behavior.

## 62. Troubleshooting: judge JSON parse error

The judge did not return valid JSON. Improve the judge prompt, use structured
output if supported, or catch parse errors and return a named judge failure
annotation.

Deterministic scores should remain available independently.

## 63. Privacy considerations

Traces may contain:

- user questions;
- tool arguments;
- tool outputs;
- model outputs;
- repository excerpts;
- source snippets.

Do not use production HR data in local Phoenix without approved controls.

## 64. Data minimization

Export only fields needed for debugging and evaluation. Bound content, redact
secrets, avoid protected attributes, and store large content as controlled
artifacts rather than span attributes.

## 65. Retention

The named Docker volume persists until explicitly deleted. Define retention,
backup, access, and deletion policies before enterprise use.

## 66. Access control

Localhost binding is not a production authorization system. Production Phoenix
requires authenticated deployment, role-based access, tenant isolation, TLS,
and audit logging.

## 67. Prompt injection in evaluation

Dataset prompts may intentionally contain attacks. They remain untrusted input.
Evaluators must not execute instructions embedded in observed output.

The optional judge prompt should delimit observed content and treat it as data.

## 68. Evaluation leakage

Repeatedly tuning prompts against the same examples can overfit the evaluation
suite.

Maintain hidden holdouts, rotate adversarial examples, and monitor real-world
distribution shift.

## 69. Evaluator quality

An evaluator is itself software. Validate:

- correctness;
- edge cases;
- agreement with human reviewers;
- sensitivity to expected changes;
- resistance to superficial wording;
- stability over time.

## 70. Turning scores into release gates

This project currently treats scores as report-only. Before making them CI
gates:

1. establish stable baselines;
2. classify flaky examples;
3. define acceptable thresholds;
4. separate blocking safety metrics from informational cost metrics;
5. require trace review for failures;
6. version judge and evaluator logic.

## 71. Suggested blocking metrics

Potential blockers:

- tool-boundary violations;
- secret access;
- missing approvals;
- prompt-injection compliance failures;
- severe completion regressions;
- missing required artifacts.

Tokens and latency usually need thresholds rather than binary equality.

## 72. Suggested quality metrics

Add:

- grounded citation validity;
- required-tool presence;
- exact agent-route alternatives;
- answer correctness;
- failure honesty;
- constraint retention;
- retry recovery;
- artifact integrity.

## 73. Suggested operational metrics

Add:

- p50/p95/p99 latency;
- tokens per successful task;
- provider-error rate;
- fallback rate;
- tool failure rate;
- redundant delegation rate;
- compaction rate;
- human-review rate.

## 74. Evaluating deep research

The current Phoenix task calls general `run_turn`, not the multi-turn HITL
deep-research workflow.

A deep-research evaluator needs a scripted sequence:

```text
initial vague query
-> checkpoint options
-> selected option
-> research round
-> selected option
-> finish
-> final report
```

It should evaluate source quality, direction diversity, user-choice adherence,
compaction fidelity, fallback behavior, and final synthesis.

## 75. Evaluating HITL

Record:

- approval requested;
- options shown;
- human selection;
- edited input;
- approved content hash;
- resumed operation;
- final side effect.

The evaluator should verify that no consequential action occurred before valid
approval.

## 76. Evaluating model fallback

Inject controlled provider failures and verify:

- primary attempts are bounded;
- fallback activates at the configured threshold;
- evidence survives the switch;
- model names are recorded;
- total latency and cost are visible;
- output remains valid.

## 77. Evaluating compaction

Create long trajectories that cross the threshold, then test:

- a compaction event occurred;
- active context shrank;
- original goal survived;
- user constraints survived;
- sources survived;
- unresolved questions survived;
- archived details remain retrievable;
- final answer remains accurate.

## 78. Evaluating safety

Include attacks in:

- user prompts;
- web snippets;
- repository comments;
- resumes;
- tool output;
- model-generated function arguments.

Measure both refusal and safe task completion; refusing everything is not a
useful system.

## 79. Production architecture

For enterprise scale, separate:

```text
evaluation scheduler
durable task queue
stateless workers
dataset registry
artifact store
trace backend
score store
comparison service
release gate
```

The local background-thread operation registry should become a durable job
system.

## 80. Reproducibility limits

Results vary because of:

- model sampling;
- provider updates;
- current web data;
- external service availability;
- timing;
- judge variability.

Record model identifiers, prompt versions, dataset version, tool versions, and
environment metadata.

## 81. Complete CLI flow

```text
python -m evals.run
|
v
main parses flags
|
v
run_evaluation
|
├── load .env
├── validate Gemini key
├── load selected JSONL examples
├── connect Phoenix client
├── upload dataset version
├── enable agents-evals telemetry
├── create deterministic evaluator list
├── optionally create LLM judge
├── create timestamped experiment
└── run_experiment
      |
      ├── task -> real run_turn
      ├── summarize trajectory
      ├── deterministic scores
      ├── optional judge scores
      └── Phoenix persistence
|
v
flush telemetry
|
v
print Phoenix URL and experiment
```

## 82. Complete UI flow

```text
browser opens Evaluations
|
v
GET /api/evals/dataset
|
v
render checkboxes and table
|
v
user selects examples, limit, judge
|
v
POST /api/evals/run
|
v
background operation starts
|
v
run_evaluation executes Phoenix experiment
|
v
browser polls operation
|
v
experiment summary and Phoenix link displayed
```

## 83. Files to read in order

1. `evals/dataset.jsonl`
2. `evals/run.py`
3. `src/agent.py`
4. `src/runtime.py`
5. `src/telemetry.py`
6. `compose.phoenix.yaml`
7. `src/gui.py`
8. `docs/EVAL_INTERNALS.md`

## 84. Beginner mental model

Think of the system as a school exam:

- JSONL dataset: question paper and answer expectations;
- task: student taking the exam;
- trajectory: recording of the student's work;
- evaluator: grading rubric;
- experiment: one exam sitting;
- Phoenix server: records office;
- Phoenix UI: report-card viewer;
- trace ID: recording identifier.

## 85. What to do before the first full run

```bash
source .venv/bin/activate
docker compose -f compose.phoenix.yaml up -d
docker build -t agents-python-sandbox:1 sandbox
open http://localhost:6006
python -m evals.run --limit 2
```

Inspect the two rows and traces before running all twenty.

## 86. Final checklist

- Phoenix container running
- Port 6006 reachable
- Gemini key configured
- Sandbox image available for Python examples
- Dataset JSONL valid
- Stable IDs unique
- Correct Phoenix project selected
- Deterministic scores inspected
- Failed rows traced
- Judge used only when needed
- Cost and latency reviewed
- Sensitive data excluded

## 87. Final summary

Phoenix is the storage, execution, and visualization layer around this
project's evaluation contract. The contract itself remains in Python and
JSONL:

```text
dataset says what should happen
task runs what actually happens
trajectory proves how it happened
evaluator measures the difference
Phoenix stores and displays everything
```

Once this separation is clear, Phoenix evaluations become much easier to
reason about. The platform is not “judging the agent magically”; it is
executing and recording the task and evaluators that this repository defines.
