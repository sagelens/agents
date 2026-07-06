# How Phoenix evaluations work internally

This document follows one evaluation example from the terminal command to the
Phoenix comparison table. It separates three systems that are easy to confuse:

1. **Our evaluation harness** decides the examples, invokes the agent, and
   computes scores.
2. **The Phoenix Python client** schedules tasks/evaluators and sends records.
3. **The Phoenix server** stores datasets, experiment runs, scores, and traces,
   then renders them at `http://localhost:6006`.

Phoenix does not decide the correct agent route. We define that contract.

## The complete call path

```text
python -m evals.run --ids weather
│
├─ evals.run.main()
│  ├─ load_examples()
│  ├─ Client(base_url="http://localhost:6006")
│  ├─ client.datasets.create_dataset(...)
│  └─ client.experiments.run_experiment(...)
│
├─ Phoenix client reads the dataset example
│
├─ task(input={"prompt": "What is the current weather in Delhi?"})
│  └─ src.agent.run_turn(...)
│     ├─ coordinator selects research_agent
│     ├─ research_agent calls get_weather
│     ├─ coordinator creates the final answer
│     └─ finish_turn() creates the trajectory
│
├─ summarize_result() converts the trajectory to experiment output
│
├─ deterministic_evaluator(output, expected)
│  └─ returns ten named score records
│
├─ Phoenix client POSTs
│  ├─ experiment run output
│  └─ experiment evaluation annotations
│
└─ Phoenix server stores them in SQLite
   └─ UI renders input | reference | output | scores
```

## Phase 1: loading our handwritten contract

The source dataset is
[`evals/dataset.jsonl`](../evals/dataset.jsonl). Each line is one independent
JSON object:

```json
{
  "id": "weather",
  "prompt": "What is the current weather in Delhi?",
  "expected": {
    "agents": ["research_agent"],
    "mode": "single",
    "tools": ["get_weather"],
    "reference": "Report current Delhi weather using the weather tool."
  }
}
```

The fields mean:

- `id`: stable identity across repeated experiments.
- `prompt`: the real input sent to the agent.
- `expected.agents`: ordered specialist route we consider correct.
- `expected.mode`: expected orchestration shape.
- `expected.tools`: low-level tools expected for this behavior.
- `reference`: human-readable intent for review or an LLM judge.
- `artifact_media_type`: optional required output, such as `image/png`.

[`load_examples()`](../evals/run.py) reads every JSON line, applies `--ids`,
then applies `--limit`. Unknown IDs are rejected. This happens before any model
call, preventing an accidentally empty or mistyped experiment.

Conceptually:

```python
examples = read_json_lines("evals/dataset.jsonl")
examples = filter_by_ids(examples)
examples = examples[:limit]
```

## Phase 2: converting to Phoenix's dataset schema

Our readable object is transformed into Phoenix's required envelope:

```python
{
    "id": example["id"],
    "input": {"prompt": example["prompt"]},
    "output": example["expected"],
    "metadata": {"suite": "framework-free-multi-agent-v1"},
}
```

This happens in [`main()`](../evals/run.py), immediately before
`client.datasets.create_dataset(...)`.

The naming can be confusing:

- Phoenix calls the known expected value dataset `output`.
- During evaluator execution, it exposes that same value through the friendlier
  parameter name `expected`.
- The newly generated agent result is also called `output` by the evaluator.

Use this mental mapping:

```text
dataset.input       -> evaluator input
dataset.output      -> evaluator expected/reference
task return value   -> evaluator output
```

`create_dataset` sends the examples to the Phoenix REST API. Reusing the dataset
name creates another dataset version rather than a completely unrelated test
suite. Stable example IDs allow comparison.

## Phase 3: creating the experiment

Our central call is:

```python
client.experiments.run_experiment(
    dataset=dataset,
    task=make_task(api_key, model),
    evaluators=[deterministic_evaluator],
    experiment_name=experiment_name,
)
```

It appears near the end of [`evals/run.py`](../evals/run.py).

At a low level, the Phoenix client:

1. Creates an experiment record for the selected dataset version.
2. Reads that version's examples.
3. Calls the supplied `task` once for each example.
4. Stores each task's return value as an experiment run.
5. Calls every evaluator for every successful task result.
6. Stores evaluator results as experiment evaluation annotations.
7. Returns a `RanExperiment` summary and prints progress.

The installed Phoenix client implements this in:

```text
phoenix/client/resources/experiments/__init__.py
phoenix/client/resources/experiments/evaluators.py
phoenix/client/resources/datasets/__init__.py
```

Those are dependency files inside the active Python environment, not files we
maintain. Important internal REST operations include:

```text
POST /v1/datasets/upload
POST /v1/datasets/<dataset-id>/experiments
POST /v1/experiments/<experiment-id>/runs
POST /v1/experiment_evaluations
```

Phoenix is therefore not secretly interpreting the answer. It is executing
Python callables we supplied and persisting their returned values.

## Phase 4: executing the real agent

[`make_task()`](../evals/run.py) returns this closure:

```python
def task(input):
    result = run_turn(api_key, model, str(uuid4()), input["prompt"])
    return summarize_result(result)
```

Phoenix recognizes the parameter name `input` and passes the dataset input:

```python
{"prompt": "What is the current weather in Delhi?"}
```

Every example gets a new UUID session. This prevents the greeting example,
weather example, or previous experiment from contaminating another example
through conversation memory.

The call goes through the production
[`run_turn()`](../src/agent.py), not a fake agent:

```text
coordinator model call
    -> zero or more specialist delegations
    -> specialist model/tool loops
    -> coordinator final synthesis
    -> finish_turn
```

This matters because the experiment exercises the real prompts, model, tools,
Docker sandbox, limits, provider retries, persistence, and failure behavior.

## Phase 5: producing the source trajectory

At the end of the agent run, [`finish_turn()`](../src/agent.py):

1. Saves user/assistant public history.
2. Sums tokens from model events.
3. Collects artifacts.
4. Creates `agent_summaries`.
5. Builds one hierarchical trajectory.
6. optionally exports OpenTelemetry spans.
7. appends the trajectory to `data/trajectories.jsonl`.
8. returns the answer, artifacts, trace ID, and trajectory.

The important trajectory event types are:

```text
model_call
model_error
delegation_start
delegation_end
tool_call
```

The evaluator does not attempt to infer routing from the prose answer. It reads
these structured events.

## Phase 6: normalizing the result

[`summarize_result()`](../evals/run.py) creates a smaller, stable experiment
output. It extracts:

```text
answer
agents
mode
tools
specialist statuses
artifacts
tokens
latency
application status
trace IDs
delegation count
coordinator model-call count
```

[`route_summary()`](../evals/run.py) computes orchestration mode from
`delegation_start` events:

```text
zero delegation starts
    -> direct

one delegation start
    -> single

multiple starts with the same parent coordinator step
    -> parallel

multiple starts with different parent coordinator steps
    -> sequential
```

Example parallel events:

```json
[
  {
    "type": "delegation_start",
    "target_agent": "research_agent",
    "parent_step_id": "coordinator-call-1"
  },
  {
    "type": "delegation_start",
    "target_agent": "codebase_agent",
    "parent_step_id": "coordinator-call-1"
  }
]
```

Because both share `coordinator-call-1`, the result is `parallel`.

## Phase 7: evaluator argument binding

Our deterministic evaluator is declared as:

```python
def deterministic_evaluator(output: dict, expected: dict):
```

Phoenix inspects these parameter names. Its client-side evaluator adapter binds:

```text
output   -> return value from task()
expected -> dataset's reference output
```

The Phoenix client also recognizes names such as `input`, `reference`,
`metadata`, `example`, and `trace_id`. This binding logic lives in the installed
Phoenix dependency's `experiments/evaluators.py`.

That is why changing our parameters to arbitrary names such as `actual_value`
and `gold_value` would not work without a wrapper. `output` and `expected` are
part of the Phoenix evaluator callable convention.

## Phase 8: deterministic score calculation

[`deterministic_evaluator()`](../evals/run.py) performs ordinary Python
comparisons. For weather:

```python
actual_agents == expected_agents
```

becomes:

```python
["research_agent"] == ["research_agent"]
```

and returns:

```json
{"name": "specialist_selection", "score": 1.0}
```

If Data Science was incorrectly chosen:

```python
["data_science_agent"] == ["research_agent"]
```

the score becomes `0.0`.

The ten returned annotations are:

```text
specialist_selection
execution_mode
tool_boundary
loop_limits
completion
artifact_requirement
path_efficiency
redundant_delegations
tokens
latency_ms
```

Several details matter:

- `tool_boundary` uses [`AGENT_SPECS`](../src/agents.py) as the authority for
  which tools each specialist may use.
- `loop_limits` reuses the production constants from
  [`src/agent.py`](../src/agent.py).
- `artifact_requirement` checks trusted artifact metadata, not model prose.
- `tokens` and `latency_ms` are measurements; they are not pass/fail scores.
- `redundant_delegations` is better when lower.

Phoenix's evaluator adapter accepts our list of dictionaries and normalizes each
dictionary into an evaluation annotation.

## Phase 9: optional LLM-as-judge

`--judge` appends the evaluator created by
[`make_judge()`](../evals/run.py).

The judge receives:

```text
expected contract
+ actual structured output
+ instructions for six qualitative dimensions
```

Gemma must return JSON:

```json
{
  "selection": 1,
  "delegation_quality": 1,
  "tool_handling": 1,
  "correctness": 1,
  "citation_and_honesty": 1,
  "conciseness": 0
}
```

The code converts this to six Phoenix annotations named `judge_*`.

This judge is different from deterministic evaluation:

```text
Deterministic:
  exact, cheap, reproducible
  example: did research_agent run?

LLM judge:
  subjective, slower, costs tokens, may vary
  example: was the final explanation concise and honest?
```

OpenTelemetry suppression surrounds the judge call so judge activity does not
become another application trace and recursively confuse the evaluation.

## Phase 10: persistence and the UI

The Compose configuration is
[`compose.phoenix.yaml`](../compose.phoenix.yaml). Phoenix runs as a non-root
container and stores SQLite data under `/mnt/data` in the named
`phoenix_data` volume.

The comparison UI is a projection of stored records:

```text
input column
    = dataset input

reference output column
    = dataset expected output

experiment column
    = task return value

annotation list
    = evaluator return values
```

For the screenshot's greeting:

```text
input:
  Hello! Briefly introduce yourself.

reference:
  agents=[], mode=direct, tools=[]

actual:
  agents=[], mode=direct, tools=[]

scores:
  specialist_selection=1
  execution_mode=1
  tool_boundary=1
  path_efficiency=1
  redundant_delegations=0
```

Repeated experiments reuse the same input/reference but store new actual
outputs and annotations. That is why Phoenix can place several experiment
columns side by side.

## Tracing and evaluation are parallel records

Evaluation records:

```text
example -> task output -> score annotations
```

Tracing records:

```text
turn -> coordinator -> delegation -> specialist -> model/tool
```

[`src/telemetry.py`](../src/telemetry.py) converts the completed application
trajectory into OpenInference spans and sends them to:

```text
POST http://localhost:6006/v1/traces
```

[`finish_turn()`](../src/agent.py) saves the returned Phoenix trace ID into the
JSONL trajectory and task output. The experiment row can therefore point from a
failed score to the execution details that produced it.

The two records answer different questions:

```text
Evaluation: Was the run good?
Trace: Why did the run behave that way?
```

## What happens when something fails

### Agent/provider failure

`run_turn` returns a structured failure status. `completion` becomes `0`, while
other observable metrics can still be saved.

### Evaluator failure

Phoenix records an evaluator error for that annotation. The task output and
other evaluator results remain available.

### Phoenix trace exporter failure

The agent still writes `data/trajectories.jsonl`. Trace export is best-effort
and cannot fail the agent turn.

### Phoenix server unavailable

Dataset upload or experiment creation cannot proceed because the evaluation
command requires Phoenix persistence. Normal `python main.py` operation still
works because evaluation is an explicit offline command.

## File map

| File | Responsibility |
|---|---|
| `evals/dataset.jsonl` | Handwritten behavioral contract |
| `evals/run.py` | Dataset upload, task, normalization, evaluators, experiment |
| `src/agent.py` | Real multi-agent execution and trajectory creation |
| `src/agents.py` | Specialist definitions and allowed tools |
| `src/runtime.py` | Specialist model/tool loop |
| `src/tools.py` | Weather, search, grep, and Python implementations |
| `src/telemetry.py` | JSONL trajectory to OpenInference spans |
| `src/store.py` | Session and trajectory persistence |
| `compose.phoenix.yaml` | Local Phoenix server and SQLite volume |
| `docs/EVALUATIONS.md` | Beginner concepts and UI navigation |

## The shortest mental model

```text
You define:
  prompt + expected behavior

Phoenix runs:
  task(prompt)

Your task returns:
  actual structured behavior

Your evaluator compares:
  actual vs expected

Phoenix stores and displays:
  input + expected + actual + scores
```

The evaluator is simply a function. Phoenix provides the repeatable execution,
versioned data, persistence, comparison UI, and linkage to traces.
