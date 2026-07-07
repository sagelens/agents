# Phoenix tracing and agent evaluations

This project keeps JSONL trajectories as its source of truth and optionally
sends a visual copy to local [Arize Phoenix](https://github.com/Arize-ai/phoenix).
The `agents-dev` project shows interactive traces. The `agents-evals` project
stores traces from deliberate offline experiments. Evaluation never runs after
a normal user turn.

## Run and edit evaluations from the local UI

Start the application and open the **Evaluations** tab:

```bash
python main.py
open http://localhost:9999
```

The UI can browse and select JSONL examples, apply a quick-run limit, enable
the optional LLM judge, and launch the real Phoenix experiment in a background
operation. Completed runs show the experiment name, selected IDs, model, and
Phoenix URL.

The **Dataset Builder** tab creates examples with expected route, agents,
tools, reference behavior, category, and difficulty. It validates unique
stable IDs and atomically updates `evals/dataset.jsonl`; the same dataset
continues to work with the CLI.

For the function-by-function implementation path, read
[How Phoenix evaluations work internally](EVAL_INTERNALS.md).
For a concrete interpretation of the latest 20-example run, including every
row, aggregate score, trace-ID layer, evaluator blind spot, and failure cause,
read [Latest Phoenix experiment analysis](PHOENIX_LATEST_EXPERIMENT_ANALYSIS.md).

## What Phoenix is, in plain language

Phoenix is a local website plus a collector and database:

```text
Your Python agent
    |
    |  OpenTelemetry spans over HTTP
    v
Phoenix collector at localhost:6006/v1/traces
    |
    v
SQLite in the phoenix_data Docker volume
    |
    v
Phoenix UI at http://localhost:6006
```

Think of it as two related tools:

1. **A flight recorder.** It shows exactly which model, agent, and tool
   operations occurred, how they were nested, how long they took, and how many
   tokens they used.
2. **A report card.** It runs the same representative questions against the
   application, stores the outputs, and attaches objective or model-generated
   scores.

Phoenix does not replace Gemma, route requests, or execute tools. Our Python
application still does all of that. Phoenix only receives records and displays
or compares them. Phoenix's official overview describes the same division:
traces explain what happened, while evaluations measure whether it was good.

### A concrete example

For this input:

```text
What is the current weather in Delhi?
```

the expected route is:

```text
coordinator
  -> research_agent
      -> get_weather
  -> coordinator final answer
```

Tracing answers questions such as:

- Did the coordinator call the Research Agent?
- Did only `get_weather` run?
- Which operation was slow?
- How many model tokens were consumed?
- Did a tool or provider fail?

Evaluation asks different questions:

- Was the selected specialist exactly `research_agent`?
- Was the route `single`, rather than direct, parallel, or sequential?
- Did the specialist stay inside its tool boundary?
- Did the turn complete?
- Did it avoid duplicate delegations?

That distinction is important: a trace is evidence about one execution; an
evaluation converts that evidence into repeatable measurements.

## Vocabulary

- **Trace:** one complete user turn.
- **Span:** one timed operation, such as a model, delegation, specialist, or tool.
- **Dataset:** a versioned collection of representative examples.
- **Example:** one input and its expected route/result.
- **Experiment:** one dataset execution against the real agent.
- **Evaluator:** code or an LLM that grades an experiment result.
- **Score:** a number; its direction says whether high or low is better.
- **Label:** a readable category such as `completed`.
- **Annotation:** a score, label, or explanation attached to a run.
- **LLM-as-judge:** a model grades output against written criteria.

A final answer can hide a wrong specialist, fabricated evidence, repeated work,
forbidden tools, or an expensive path. The suite therefore evaluates routing,
arguments, tool boundaries, path convergence, artifacts, synthesis, limits,
tokens, and latency.

## How this project implemented Phoenix

The integration has four layers:

### 1. Phoenix server

`compose.phoenix.yaml` starts the pinned Phoenix 17.5.0 non-root image. A tiny
initializer gives Phoenix's non-root UID permission to its named volume. The
server exposes only these localhost ports:

- `6006`: web UI, REST API, and OTLP-over-HTTP trace collector.
- `4317`: OTLP gRPC collector.

`PHOENIX_TELEMETRY_ENABLED=false` prevents Phoenix itself from sending product
telemetry. `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false` prevents the UI from loading
external resources. The named `phoenix_data` volume contains SQLite state.

### 2. Application tracing

`src/telemetry.py` registers an OpenTelemetry exporter only when
`PHOENIX_ENABLED=true`. At the end of `run_turn`, it translates the already
completed JSONL trajectory into OpenInference spans:

| Application operation | Span kind | Parent |
|---|---|---|
| Complete user turn | `AGENT` | none; this is the trace root |
| Coordinator model call | `LLM` | user turn |
| Delegation | `AGENT` | user turn |
| Specialist execution | `AGENT` | delegation |
| Specialist model call | `LLM` | specialist |
| Weather/search/grep/Python call | `TOOL` | specialist |

The exporter includes bounded inputs and outputs, IDs, agent depth, status,
model, token counts, latency, and artifact metadata. It never exports the API
key or the process environment. Parallel branches retain their own timestamps,
so their overlap is visible.

This export is deliberately best-effort. If Phoenix is stopped, the exporter
returns `None`; `run_turn` still saves the JSONL trajectory and answers the user.

### 3. Dataset and experiment task

`evals/dataset.jsonl` defines twelve examples. Every line has:

```json
{
  "id": "weather",
  "prompt": "What is the current weather in Delhi?",
  "expected": {
    "agents": ["research_agent"],
    "mode": "single",
    "tools": ["get_weather"]
  }
}
```

`evals/run.py` uploads these records as a Phoenix dataset named
`framework-free-multi-agent-v1`. The stable `id` lets the same logical example
be compared across dataset versions.

Phoenix calls our task once per example. The task creates a fresh session,
calls the real `src.agent.run_turn`, then returns a compact output:

```json
{
  "answer": "...",
  "agents": ["research_agent"],
  "mode": "single",
  "tools": ["get_weather"],
  "status": "completed",
  "tokens": {"total": 1234},
  "latency_ms": 2500,
  "phoenix_trace_id": "..."
}
```

It is not a mock and it does not call a simplified evaluation-only agent.
Consequently, web, Gemini, and Docker availability affect examples exactly as
they affect the CLI.

### 4. Evaluators

After each task output, Phoenix calls `deterministic_evaluator(output,
expected)`. It returns ten named annotations. Most are binary `0` or `1`.
Tokens and latency are measurements rather than pass/fail criteria.

With `--judge`, `make_judge` creates a Phoenix Evals `LLM` using the configured
Gemma model. It asks Gemma for six qualitative scores. Judge instrumentation is
suppressed so a judging call does not recursively become another application
trace.

The complete flow is:

```text
dataset example
    |
    v
real run_turn in a fresh session
    |
    +--> agents-evals trace
    |
    v
structured experiment output
    |
    +--> deterministic evaluator (always)
    |
    `--> Gemma judge evaluator (only with --judge)
             |
             v
experiment row + score annotations
```

## Start and stop Phoenix

```bash
docker compose -f compose.phoenix.yaml up -d
open http://localhost:6006
```

Stop it while preserving its SQLite volume:

```bash
docker compose -f compose.phoenix.yaml down
```

Erase all Phoenix data only intentionally:

```bash
docker compose -f compose.phoenix.yaml down -v
```

The small Compose initializer only fixes named-volume ownership. The Phoenix
server itself runs as the pinned image's non-root user, and its SQLite data
survives normal `down`/`up` cycles.

## Trace interactive conversations

Set these values in `.env`, restart the CLI, and open the `agents-dev` project:

```dotenv
PHOENIX_ENABLED=true
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
PHOENIX_PROJECT_NAME=agents-dev
```

```text
multi_agent_turn (AGENT)
├── coordinator.model.1 (LLM)
├── delegate.research_agent (AGENT)
│   └── research_agent (AGENT)
│       ├── research_agent.model.1 (LLM)
│       └── research_agent.get_weather (TOOL)
└── coordinator.model.2 (LLM)
```

Independent specialists are sibling branches with overlapping timestamps.
Dependent work appears in separate coordinator rounds. The JSONL record stores
`phoenix_trace_id` for cross-navigation. Export failure never fails a turn.

## Run evaluations

Phoenix and the Gemini key must be available:

```bash
python -m evals.run
python -m evals.run --limit 2
python -m evals.run --ids chart,parallel_research_code
python -m evals.run --judge
```

The first three forms use deterministic scoring. `--judge` adds qualitative
Gemma scores for selection, delegation quality, tool handling, correctness,
citation/failure honesty, and conciseness. Each example receives an isolated
session and calls the production `run_turn` entry point.

The twelve stable examples live in `evals/dataset.jsonl`. Re-running uploads a
comparable dataset version and creates a new named experiment. Scores are
report-only; they are not CI gates.

`--limit 2` uploads and evaluates only two examples in the newest dataset
version. Therefore, after a quick run the UI may say `Examples 2`, not 12. Run
`python -m evals.run` without a limit to create the full twelve-example version.

## Navigate the Phoenix UI on port 6006

Open [http://localhost:6006](http://localhost:6006). Phoenix 17.5.0 has these
important left-sidebar items:

- **Tracing**: application projects, traces, and span trees.
- **Datasets & Experiments**: examples, experiment runs, and scores.
- **Playground**: interactive prompt experimentation; this project does not use
  it for its Python multi-agent experiment.
- **Evaluators**: reusable evaluators configured in the Phoenix UI.

The sidebar may show **Evaluators 0** even though an experiment has ten score
columns. That is expected: our code evaluators are supplied by `evals/run.py`
for that experiment and become experiment annotations; they are not saved as
reusable UI evaluator definitions.

### See experiment scores

1. Click **Datasets & Experiments**.
2. Click **framework-free-multi-agent-v1**.
3. The **Experiments** tab opens by default.
4. Find an experiment such as `agents-eval-20260706-064943`.
5. Read its summary columns:
   `specialist_selection`, `execution_mode`, `tool_boundary`, `completion`,
   `path_efficiency`, `tokens`, and `latency_ms`.
6. Click the experiment name to open the comparison page.

The experiment summary shows averages across all examples. A mean of `0.75`
for a binary evaluator means 75% of examples scored 1. A token or latency
column is a raw average and must not be interpreted as a pass percentage.

### Inspect one example

On the experiment comparison page:

1. Keep **Grid** selected for the easiest first view.
2. Each row contains three important blocks:
   - **input**: the user prompt;
   - **reference output**: the expected agents, mode, tools, and reference;
   - **experiment output**: the actual answer, route, metrics, and trace ID.
3. Below the actual output, inspect annotation chips such as
   `specialist_selection 1.00` or `completion completed (1.00)`.
4. Click **Show more** if the JSON output is collapsed.
5. Click **View example run details** for the complete stored task result.
6. Click **View run trace** to move from the score to the execution that
   produced it.

Use the filter box to isolate failures. The UI provides a sample expression;
for example, filtering an annotation to a zero score narrows the table to that
failure type. You can also click **Filter by annotation** beside a score.

### Compare experiments

Run the evaluation before and after a prompt or routing change. On the dataset:

1. Select the experiments you want to compare.
2. Open their comparison view.
3. Use **Grid** for example-by-example output.
4. Use **List** for a compact result list.
5. Use **Metrics** for aggregate score comparisons.

Compare quality scores together with tokens and latency. A change that raises
correctness but doubles tokens is a real tradeoff rather than an automatic win.

### Inspect traces directly

1. Click **Tracing**.
2. Open **agents-dev** for normal CLI conversations or **agents-evals** for
   evaluation executions.
3. Select a trace whose root is `multi_agent_turn`.
4. Expand the span tree.
5. Click a span to inspect attributes, input/output, timestamps, status, and
   tokens.

What you should see:

- A greeting has the root plus a coordinator `LLM` span and no delegation.
- A weather question has a Research delegation, Research specialist, model,
  and `get_weather` tool span.
- Parallel work has sibling delegation branches with overlapping times.
- Sequential Research → Data Science work has ordered branches from separate
  coordinator rounds.
- Tool spans appear only beneath the specialist permitted to use that tool.

The experiment page also has a **Traces** link for its trace project. Prefer
**View run trace** on an individual row when debugging one failed example,
because it preserves the exact example-to-execution connection.

### A practical failure-debugging recipe

Suppose `specialist_selection` is `0`:

1. Compare `reference output.agents` with `experiment output.agents`.
2. Open **View run trace**.
3. Inspect the first coordinator `LLM` span and delegation span.
4. Check the delegation task/context that the coordinator wrote.
5. If routing was correct but the final answer was poor, move down to the
   specialist model and tool spans.
6. Change the relevant prompt or code.
7. Re-run the same stable example:

   ```bash
   python -m evals.run --ids weather
   ```

8. Compare the new experiment with the old one.

## Deterministic scores

- `specialist_selection`: exact ordered specialist list.
- `execution_mode`: direct, single, parallel, or sequential.
- `tool_boundary`: selected specialists own every observed tool.
- `loop_limits`: coordinator and delegation budgets were respected.
- `completion`: normal completion rather than a provider/tool failure.
- `artifact_requirement`: a required PNG or other artifact exists.
- `path_efficiency`: useful work without extra delegation.
- `redundant_delegations`: repeated work; lower is better.
- `tokens` and `latency_ms`: neutral measurements, not quality gates.

Open a failed experiment row, compare `output` with `expected`, then use its
`phoenix_trace_id` to inspect the first incorrect span.

## Gemma judge cost and bias

`--judge` makes extra Gemini calls. Reusing Gemma is convenient but can repeat
the agent's preferences and mistakes, so its scores are review signals rather
than truth. A judge/provider/parsing failure becomes an evaluator error while
deterministic output remains available. Judge calls receive no tracer, avoiding
recursive evaluation traces.

## Extend the suite

Add one JSON object line to `evals/dataset.jsonl` with a unique `id`, `prompt`,
and `expected` object. Add observable rules to `deterministic_evaluator` in
`evals/run.py`. Never evaluate or store private chain-of-thought.

## Privacy and troubleshooting

Phoenix stores bounded prompts, answers, delegation tasks, tool arguments, and
tool results in its local volume. It never receives API keys or `.env` contents.
Review this data before sharing it.

- No UI: run `docker compose -f compose.phoenix.yaml ps`.
- Dataset upload fails: start Phoenix before `python -m evals.run`.
- No CLI traces: set `PHOENIX_ENABLED=true` and restart.
- Chart failure: start Docker Desktop and build the sandbox image.
- Phoenix down but agent works: intentional; JSONL remains complete.

Further reading: [tracing](https://arize.com/docs/phoenix/tracing),
[experiments](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments/run-experiments),
and [agent evaluation](https://arize.com/ai-agents/agent-evaluation/).

## Official sources used for this implementation

- [What Phoenix is](https://arize.com/docs/phoenix)
- [How tracing and OTLP work](https://arize.com/docs/phoenix/tracing/concepts-tracing/how-does-tracing-work)
- [Traces, spans, projects, and span kinds](https://arize.com/docs/phoenix/tracing/concepts-tracing/what-are-traces)
- [Running and inspecting experiments](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments/run-experiments)
- [Code and LLM evaluators](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments/using-evaluators)
