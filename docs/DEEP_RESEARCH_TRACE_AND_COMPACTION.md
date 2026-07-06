# Deep-Research Trace and Dynamic Compaction Walkthrough

## 1. What this document explains

This guide reconstructs the supplied run:

```text
run_id: f85917cd-33aa-4784-8862-4920550977a2
route: deep_research_agent
status: completed
sources: 20
token estimate: 4,978
compactions: 0
```

It explains:

- which Python scripts executed;
- which functions and current source lines ran;
- how user-selected Tree-of-Thought branches controlled the workflow;
- how Gemma failures caused a Gemini Flash fallback;
- how web results became durable sources and compact memory;
- why this particular run did not trigger compaction;
- how dynamic compaction works in this codebase;
- how compaction is commonly implemented in larger AI-agent harnesses.

Line numbers refer to the current code at the time this document was written.

## 2. High-level result

The user researched:

```text
How does dynamic compaction work in AI agents and AI harnesses?
```

The resulting path was:

```text
Initial question
    |
    v
Four research directions
    |
    +-- KV cache and memory management
    +-- Recursive state summarization  <-- selected
    +-- Harness orchestration
    +-- Information-loss analysis
             |
             v
    Three new directions + Finish
             |
             +-- Generic open question 3  <-- selected
             |
             v
    Three evidence-driven directions + Finish
             |
             +-- Finish and synthesize  <-- selected
```

Two stateless research-agent invocations executed. Together they issued four
web searches and collected twenty unique URLs. The user then selected the
application-controlled finish branch.

## 3. Scripts involved

| Script | Role in this run |
|---|---|
| `src/gui.py` | Receives the browser query and later direction selections; runs work in background threads; returns state to the browser. |
| `src/deep_research.py` | Owns the durable research state machine, checkpoints, source ledger, compact brief, fallback calls, compaction, and final synthesis. |
| `src/agents.py` | Defines the web-only `DEEP_RESEARCH_AGENT`. |
| `src/prompt.py` | Defines its stateless, focused, untrusted-web-data instructions. |
| `src/runtime.py` | Executes each specialist's bounded model/tool loop and model fallback. |
| `src/tools.py` | Executes `web_search` and normalizes results. |
| `src/store.py` | Saves and reloads `data/research/<run-id>.json`. |
| `src/logging_config.py` | Emits structured live logs to the terminal and right UI panel. |

No generic coordinator delegation, data-science agent, codebase agent, Docker
tool, workflow DAG, resume agent, or outreach agent executed.

## 4. Browser and HTTP flow

### Initial submission

The browser function `start()`:

1. Reads the query.
2. Resets any previous research-run ID.
3. Displays “Planning and selecting agents.”
4. Sends `POST /api/query`.
5. Receives an operation ID.
6. polls `/api/operations/<operation-id>` every 700 ms.

Code:

- `src/gui.py`, lines 221–234
- `src/gui.py`, lines 205–219 for polling

### Server handling

The HTTP handler:

1. Parses a bounded JSON body.
2. reads `GEMINI_API_KEY` and `GEMINI_MODEL`;
3. validates the query and browser-session UUID;
4. launches a daemon background thread;
5. immediately returns HTTP 202.

Code:

- `src/gui.py`, lines 509–529
- `src/gui.py`, lines 395–436 for background operations

### Dynamic route

`_route_query` chooses between:

- `general_coordinator`;
- `deep_research`.

The request was routed to deep research because it called for a broad,
iterative investigation with user-selectable directions.

Code:

- `src/gui.py`, lines 332–374
- `src/gui.py`, lines 377–386 for the deep-research branch

The final pasted state uses the stable public label:

```json
{
  "kind": "research",
  "route": "deep_research_agent"
}
```

That public shape is constructed in `src/gui.py`, lines 295–313.

## 5. Run creation

`start_deep_research` executes at `src/deep_research.py`, lines 179–206.

It creates:

```json
{
  "status": "created",
  "original_query": "...",
  "current_focus": "...",
  "research_brief": "",
  "unresolved_questions": [],
  "selected_directions": [],
  "source_ledger": [],
  "subagent_summaries": [],
  "checkpoints": [],
  "compaction_history": [],
  "token_estimate": 0,
  "final_report": "",
  "events": [{"type": "research_created"}]
}
```

The generated run ID was:

```text
f85917cd-33aa-4784-8862-4920550977a2
```

The state is saved before the first model call:

- `src/deep_research.py`, line 203
- `src/store.py`, lines 91–97

Persistence uses a temporary file followed by an atomic replacement. This
reduces the chance of leaving a partially written primary JSON file.

## 6. First Tree-of-Thought checkpoint

`_make_checkpoint` runs at `src/deep_research.py`, lines 143–176.

It sends `_active_context(run)` to the model and requests four distinct
directions. `_active_context` is defined at lines 90–99 and includes only:

- original query;
- current focus;
- compact brief;
- unresolved questions;
- selected path;
- three recent specialist summaries;
- source ledger.

The first options were:

1. KV Cache and Memory Management
2. Recursive State Summarization
3. Harness Orchestration and Resource Allocation
4. Information Loss and Fidelity Analysis

`_normalize_directions`, lines 106–140:

- validates model JSON;
- bounds field lengths;
- assigns UUIDs;
- keeps exactly four choices;
- generates generic fallback choices if necessary.

The checkpoint was saved with status `pending`, then the run changed to
`waiting_for_human`.

Trace:

```text
15:55:33 research_created
15:56:04 research_directions_proposed
checkpoint: a42556ec-fc9a-4bf8-9adc-2123f0257023
```

## 7. First human selection

The user chose:

```text
Recursive State Summarization
```

with focus:

```text
The algorithmic process of transforming raw token sequences into compact,
high-utility semantic summaries.
```

The browser sends `POST /api/research/select`:

- `src/gui.py`, lines 236–250
- `src/gui.py`, lines 530–548

`select_research_direction`, `src/deep_research.py`, lines 332–419:

1. Finds the pending checkpoint.
2. validates the option number;
3. marks the checkpoint selected;
4. stores feedback;
5. appends the selection to the durable path;
6. sets `current_focus`;
7. changes status to `running`;
8. saves before external work.

The selection was recorded at lines 343–360.

## 8. Focused specialist delegation

The coordinator builds a task containing:

- original broad query;
- selected focus;
- optional feedback;
- search-result budget;
- instructions to return findings, uncertainty, and URLs.

It passes only compact context:

```json
{
  "original_query": "...",
  "research_brief": "",
  "unresolved_questions": []
}
```

Code:

- `src/deep_research.py`, lines 371–390

The specialist is launched at lines 391–401 using `DEEP_RESEARCH_AGENT`.

Each invocation is stateless. `run_specialist`, `src/runtime.py`, lines 99–145:

- creates a new agent-run UUID;
- creates a fresh Gemini client;
- starts empty evidence and event lists;
- builds a focused one-message conversation;
- exposes only the agent's allowed tools.

## 9. First specialist model and tool sequence

Agent run:

```text
2146e59e-2efb-46df-a71d-3daf930b8192
```

### First Gemma failure

Trace:

```text
15:56:24 model_error
model: gemma-4-31b-it
call: 1
attempt: 1
HTTP status: 500
```

The runtime records provider errors at `src/runtime.py`, lines 188–240.
Because this was the first primary-model failure, Gemma was retried.

### Successful Gemma tool plan

Trace:

```text
15:56:39 model_call
decision: call_tool
model: gemma-4-31b-it
tools: web_search × 3
```

Model-call events are built at `src/runtime.py`, lines 254–274.

The model produced three searches:

1. `"dynamic compaction" AI agents token sequences semantic summaries`
2. `algorithmic process transforming raw token sequences into compact semantic summaries AI agents`
3. `"dynamic compaction" LLM memory management token pruning summarization`

Each returned five results.

The runtime executes function calls sequentially at `src/runtime.py`, lines
306–380. It validates every requested tool against the agent allowlist before
calling it.

`web_search` is implemented in `src/tools.py`. It:

- rejects empty input;
- clamps results to one through five;
- calls the search provider;
- returns normalized title, URL, and snippet objects.

### Quality observation

Some first-query results concern geotechnical soil compaction rather than
agent-context compaction. The source ledger preserves them because the search
provider returned them, but the compact-memory and synthesis prompts are
responsible for separating relevant evidence from lexical false positives.
This illustrates why search queries and source filtering matter.

## 10. Model fallback during the first specialist run

After web searching, specialist model call 2 failed on Gemma:

```text
15:56:47 model_error
model: gemma-4-31b-it
primary failure count: 2
```

The specialist runtime keeps `primary_model_failures` across the invocation:

- initialization: `src/runtime.py`, lines 125–126;
- attempt budget: lines 170–174;
- failure increment: lines 188–193;
- fallback condition: lines 213–223.

On the second Gemma failure:

```text
active_model = gemini-2.5-flash
```

The successful retry produced:

```text
15:56:51 model_call
number: 2
decision: final_answer
model: gemini-2.5-flash
tokens: 2,930
```

The fallback did not discard the three web-search results. The same specialist
invocation retained its task, evidence list, events, and agent-run ID.

## 11. Source merge and compact-memory refresh

After the specialist returns, `select_research_direction`:

1. stores its bounded summary;
2. merges structured search evidence;
3. refreshes the compact brief;
4. extends the durable event list;
5. checks compaction;
6. creates the next checkpoint.

Code:

- `src/deep_research.py`, lines 402–418

### Source merge

`_merge_sources`, lines 220–244:

- reads actual tool evidence;
- extracts URL, title, snippet, query, and timestamp;
- deduplicates by URL;
- applies the configured source limit.

This first round supplied fifteen search results.

### Memory refresh

`_refresh_brief`, lines 247–271, asks the model to rewrite:

- `research_brief`;
- `unresolved_questions`.

The prompt explicitly asks it to retain disagreement and uncertainty. The
brief is bounded to 12,000 characters and open questions to ten items.

This is a compaction-like operation even when the threshold compactor does not
fire: raw specialist evidence is transformed into a smaller working-state
summary.

## 12. Second checkpoint and fallback directions

The second checkpoint was:

```text
0805eb5f-13c0-4826-9168-792e57101975
```

Its three research options were generic:

- Investigate open question 1
- Investigate open question 2
- Investigate open question 3

plus Finish.

These labels come from application fallback generation in
`_normalize_directions`, lines 121–130. This means the direction-generation
model returned missing, invalid, or insufficient usable direction objects.
The application maintained the four-option HITL contract instead of crashing.

The user selected open question 3.

## 13. Second specialist invocation

Agent run:

```text
fecb9ed0-0261-400a-aaa9-2937df8a1941
```

Because sub-agents are stateless, its failure counter started at zero.

### Two Gemma failures

```text
15:58:13 Gemma call 1, attempt 1 -> HTTP 500
15:58:15 Gemma call 1, attempt 2 -> HTTP 500
```

The second failure triggered Flash.

### Flash tool call

```text
15:58:16 model_call
model: gemini-2.5-flash
decision: call_tool
```

It searched:

```text
dynamic compaction vs sliding window memory vs recursive summarization LLM
```

The search returned five results.

### Flash synthesis

```text
15:58:26 model_call
number: 2
model: gemini-2.5-flash
decision: final_answer
```

The second summary and its evidence were merged using the same lines 402–418.
The final deduplicated source count became twenty.

## 14. Third checkpoint and finish

The third checkpoint contained evidence-driven choices:

1. Importance-Based Filtering Heuristics
2. Quantifying Semantic Loss
3. Computational Overhead Analysis
4. Finish and synthesize

The user selected Finish.

`select_research_direction` recognizes the reserved application focus:

```python
if option["focus"] == "__finish__":
```

at `src/deep_research.py`, lines 361–363.

No third specialist was launched.

`_synthesize`, lines 308–329:

1. serializes active context;
2. calls the model with fallback coverage;
3. requests a cited Markdown report;
4. stores `final_report`;
5. changes status to `completed`;
6. records `research_completed`;
7. saves the run.

The completion event occurred at:

```text
2026-07-06T16:00:49.721073+00:00
```

The supplied pasted object does not include a `final_report` field, although
the persisted run and normal GUI response store it.

## 15. Why `compaction_count` is zero

The completed run reports:

```text
token_estimate: 4,978
compaction_count: 0
```

The default threshold is:

```text
DEEP_RESEARCH_COMPACTION_TOKENS=12000
```

At each round, `_compact_if_needed`:

1. builds active context;
2. estimates its token size;
3. compares it with the threshold.

Code:

- active context: `src/deep_research.py`, lines 90–99;
- estimation: lines 102–103;
- comparison: lines 274–283.

Because 4,978 is below 12,000, the function returned here:

```python
if estimate < threshold:
    return
```

Therefore:

- no summaries moved into `compaction_history`;
- no `research_context_compacted` event was emitted;
- the run still retained both active specialist summaries.

This trace researched dynamic compaction as a topic, but it did not itself
cross the threshold required to perform archival compaction.

## 16. Dynamic compaction in this implementation

### The problem

An agent accumulates:

- conversation messages;
- tool requests and results;
- research summaries;
- sources;
- decisions;
- user corrections;
- unresolved questions.

Sending all raw history on every model call eventually causes:

- context-window overflow;
- increased latency and cost;
- distraction from relevant evidence;
- greater prompt-injection exposure;
- poor retrieval of facts buried in old text.

### Working memory

This implementation does not send the whole persisted run. `_active_context`
selects a working set:

```text
original query
current focus
research brief
unresolved questions
selected directions
latest three specialist summaries
source ledger
```

Everything else—complete checkpoint history, lifecycle events, and compaction
archives—stays durable but normally remains outside model prompts.

This is the first compaction layer: **projection**. It selects useful state
instead of serializing the entire object.

### Token estimate

`_estimate_tokens`:

1. serializes active context as JSON;
2. measures UTF-8 bytes;
3. divides by four.

This is an inexpensive approximation:

```python
len(serialized_bytes) // 4
```

It avoids a tokenizer dependency but is not exact. Different languages,
punctuation, code, and model tokenizers can produce different true counts.

### Trigger

Compaction is dynamic because it uses current estimated context size:

```text
if estimated tokens >= configured threshold:
    compact
```

It is not triggered after a fixed number of rounds. One source-heavy round may
compact sooner than several small rounds.

### Archival movement

When triggered, lines 284–296:

1. take all specialist summaries except the newest;
2. copy them into a new `compaction_history` record;
3. record compaction ID, timestamp, and pre-compaction token estimate;
4. keep only the newest summary in active memory.

This operation is not deletion. Older summaries remain on disk.

### Semantic refresh

After removing older summaries from active memory, line 297 calls
`_refresh_brief` using the latest summary. This asks the model to preserve the
facts and unresolved issues required for continued work.

### Re-estimation and event

Lines 298–305:

1. estimate the reduced active context;
2. emit `research_context_compacted`;
3. save the run;
4. log the new estimate.

The desired invariant is:

```text
small active prompt + complete durable audit history
```

## 17. Working memory versus archival memory

```text
Persisted research run
|
├── Working memory sent to models
|   ├── compact research brief
|   ├── unresolved questions
|   ├── selected path
|   ├── recent summaries
|   └── source ledger
|
└── Archival memory kept outside prompts
    ├── old detailed summaries
    ├── checkpoint history
    ├── lifecycle events
    ├── tool/model traces
    └── compaction records
```

Working memory optimizes model performance. Archival memory optimizes
recoverability, debugging, and auditability.

## 18. How AI harnesses generally implement compaction

An AI harness is the orchestration layer around a model. It owns messages,
tools, agent calls, permissions, persistence, and token budgets. Compaction is
usually implemented by the harness rather than by the base model.

### Pattern 1: Sliding-window truncation

Keep:

- system instructions;
- the latest N messages;
- perhaps the original user goal.

Drop older messages.

Advantages:

- cheap;
- deterministic;
- no additional model call.

Risks:

- silently loses early requirements;
- loses decisions and failed approaches;
- poor for long tasks.

### Pattern 2: Prefix summarization

When the prompt nears a limit:

1. split old and recent messages;
2. summarize the old prefix;
3. replace it with one summary message;
4. retain recent messages verbatim.

This repository's brief plus recent summaries resembles this pattern.

### Pattern 3: Recursive summarization

Repeatedly merge:

```text
previous compact summary + new events -> new compact summary
```

This supports indefinite sessions but creates summary drift. Important facts
can become shorter, weaker, or disappear after repeated rewriting.

`_refresh_brief` implements a bounded version of this pattern.

### Pattern 4: Structured-state extraction

Instead of writing prose only, extract typed fields:

```json
{
  "goal": "...",
  "constraints": [],
  "decisions": [],
  "open_questions": [],
  "sources": [],
  "artifacts": []
}
```

Structured state is easier to validate and selectively include. This deep
research run uses both prose (`research_brief`) and structured collections.

### Pattern 5: Importance-based filtering

Score each memory item by factors such as:

- relevance to the current task;
- recency;
- user-authored versus model-authored origin;
- decision or constraint status;
- source confidence;
- dependency on future work;
- uniqueness;
- tool-result authority.

Only high-scoring items enter the prompt. Low-scoring items remain archived.

### Pattern 6: Retrieval-backed memory

Store old events in a database or vector index. At each turn:

1. embed or search the current task;
2. retrieve relevant old memories;
3. inject only those memories.

This scales better than one growing summary but introduces retrieval misses and
ranking errors.

### Pattern 7: Hierarchical memory

Maintain several levels:

```text
raw events
-> per-step summaries
-> per-round summaries
-> session summary
-> durable user/project memory
```

Agents access the smallest appropriate level and drill down only when needed.

### Pattern 8: Tool-output compaction

Large tool outputs are especially expensive. Harnesses may retain:

- query and provenance;
- selected rows or excerpts;
- hashes and artifact paths;
- a concise result summary.

The full output remains in artifact storage rather than the prompt.

### Pattern 9: Branch-aware compaction

In multi-agent systems, each branch has isolated context. The parent receives
only a summary and structured evidence. This repository's stateless specialist
boundary is branch-aware compaction: raw worker conversations never enter the
parent's public memory.

## 19. What must never be compacted away

A reliable harness should preserve:

- system and safety instructions;
- the user's original goal;
- explicit constraints and corrections;
- irreversible-action approvals;
- current task state;
- unresolved failures;
- source provenance;
- artifact identifiers;
- decisions that downstream steps depend on.

These items may be represented compactly, but their meaning must remain.

## 20. Semantic-loss risks

Compaction is normally lossy. Common failure modes include:

- removing a “do not” constraint;
- merging conflicting sources into a false consensus;
- losing numeric precision;
- forgetting why an approach failed;
- replacing provenance with an unsupported claim;
- dropping a pending task;
- recursively amplifying an earlier summary error.

Mitigations include:

- structured required fields;
- keeping source URLs and raw artifacts;
- preserving contradictions explicitly;
- limiting recursive summary depth;
- validating that critical fields survived;
- allowing retrieval of archived details;
- recording pre/post-compaction token counts.

## 21. Cost and latency trade-off

Compaction saves tokens on future calls but may require an extra model call.

Conceptually:

```text
net benefit =
future prompt tokens avoided
- compaction prompt/output tokens
- extra latency
- risk cost from semantic loss
```

Compaction is valuable when enough future calls remain to amortize its cost.
Compacting immediately before final completion may cost more than it saves.

This implementation checks after each research round, when additional
checkpoints and specialist calls are still possible.

## 22. Reading future compaction traces

Look for:

```text
research_context_compacted
```

Then inspect:

1. `token_estimate_before` in the newest `compaction_history` entry;
2. post-compaction `token_estimate`;
3. `archived_subagent_summaries`;
4. updated `research_brief`;
5. preserved sources and selected directions.

If `compaction_count` remains zero, compare `token_estimate` with
`DEEP_RESEARCH_COMPACTION_TOKENS`.

## 23. Complete chronological trace

```text
15:55:33  Research run created
15:56:04  Initial four directions proposed
15:56:22  User selects Recursive State Summarization
15:56:24  Gemma specialist call fails once
15:56:39  Gemma requests three web searches
15:56:42  Search 1 returns five results
15:56:44  Search 2 returns five results
15:56:46  Search 3 returns five results
15:56:47  Gemma fails a second time
15:56:51  Gemini 2.5 Flash synthesizes specialist result
            Sources merged
            Compact brief refreshed
            Token threshold checked; no compaction
15:58:02  Second checkpoint proposed
15:58:12  User selects open question 3
15:58:13  Gemma attempt 1 fails
15:58:15  Gemma attempt 2 fails
15:58:16  Flash requests one web search
15:58:19  Search returns five results
15:58:26  Flash synthesizes specialist result
            Sources merged to twenty unique URLs
            Compact brief refreshed
            Token threshold checked; no compaction
15:59:44  Third checkpoint proposed
15:59:56  User selects Finish and synthesize
16:00:49  Final report stored; run completed
```

## 24. Key conclusions

This run demonstrates:

- human-guided Tree-of-Thought branching;
- stateless specialist isolation;
- structured web evidence;
- source deduplication;
- recursive compact-memory refresh;
- two-failure Gemma-to-Flash fallback;
- durable checkpoint/resume state;
- explicit user-controlled completion.

It does **not** demonstrate an actual threshold compaction event because its
4,978-token estimate stayed below the 12,000-token default. The code path for
actual archival compaction is `src/deep_research.py`, lines 274–305.
