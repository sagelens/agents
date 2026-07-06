# Deep Research Agent: Beginner-Friendly Code and Flow Guide

## 1. What the agent does

The deep research agent turns a broad question into a guided, multi-round
research session. It does not immediately search everything and produce one
answer. Instead, it repeatedly:

1. Proposes four useful research directions.
2. Pauses for the user to select one direction.
3. Accepts optional corrections or constraints from the user.
4. Launches a fresh, isolated web-research specialist.
5. Saves sources and a concise specialist summary.
6. Updates compact working memory.
7. Proposes another set of directions.
8. Finishes when the user selects **Finish and synthesize**, or the configured
   maximum number of rounds is exceeded.

The run is saved at every important transition, so a user can leave at a
human-review checkpoint and resume later.

The implementation is framework-free. It follows the architectural shape of a
Google ADK coordinator with task-mode sub-agents, but all orchestration is
ordinary, visible Python.

## 2. Files and responsibilities

| File | Responsibility |
|---|---|
| `main.py` | Recognizes CLI commands, displays choices, collects human feedback, and prints the final report. |
| `src/deep_research.py` | Owns research state, direction generation, delegation, memory, compaction, and synthesis. |
| `src/runtime.py` | Runs one stateless specialist with bounded model and tool rounds. |
| `src/agents.py` | Defines `DEEP_RESEARCH_AGENT`, its allowed tools, prompt, and limits. |
| `src/prompt.py` | Instructs the specialist to ground findings and treat web text as untrusted. |
| `src/tools.py` | Implements the normalized `web_search` tool. |
| `src/store.py` | Atomically saves and reloads research state. |
| `.env.example` | Documents configurable research budgets. |

## 3. Architecture overview

```text
                         public interaction
                                |
                                v
                  +---------------------------+
                  | CLI and HITL in main.py   |
                  +---------------------------+
                                |
                      start/resume/select
                                |
                                v
                +-------------------------------+
                | Deep research coordinator     |
                | src/deep_research.py          |
                +-------------------------------+
                   |           |             |
             propose paths     |          save/load
                   |           |             |
                   v           |             v
             +----------+      |    +----------------------+
             | Gemini   |      |    | data/research/*.json |
             +----------+      |    +----------------------+
                               |
                      focused task + brief
                               |
                               v
                  +----------------------------+
                  | Stateless research agent   |
                  | src/runtime.py             |
                  +----------------------------+
                               |
                               v
                         +------------+
                         | web_search |
                         +------------+
                               |
                               v
                    evidence + concise summary
                               |
                               v
                  source ledger + compact memory
```

The coordinator owns durable memory, branching, and the public conversation.
A specialist receives one focused task and returns a bounded result. It never
becomes the public conversational owner.

This resembles an ADK task sub-agent:

- The parent chooses a task.
- The sub-agent runs inside an isolated invocation.
- The sub-agent has restricted tools.
- Its result returns to the parent automatically.
- Only the parent pauses for human input.

## 4. End-to-end flow

### 4.1 Starting research

The user enters:

```text
/research How will AI agents change enterprise software?
```

`main.py` recognizes `/research ` and calls:

```python
start_deep_research(api_key, model, query)
```

`start_deep_research`:

1. Trims and validates the query.
2. Creates a unique `run_id`.
3. Initializes the durable run dictionary.
4. Saves it immediately.
5. Calls `_make_checkpoint`.
6. Returns the run to the CLI.

The initial run contains the query, empty memory collections, timestamps,
status, an event list, and an empty final report. The first checkpoint contains
four research choices and no finish choice because no evidence exists yet.

### 4.2 Producing research directions

`_make_checkpoint` calls `_model_json` with `_active_context(run)`. The model is
asked to produce directions that reduce uncertainty, improve evidence
coverage, or challenge assumptions.

The expected JSON is:

```json
{
  "directions": [
    {
      "label": "Adoption barriers",
      "outcome": "Identify organizational and technical blockers.",
      "focus": "Research current enterprise AI-agent adoption barriers."
    }
  ]
}
```

`_model_json` requests JSON output and returns an empty dictionary when parsing
fails. `_normalize_directions` then:

- ignores non-object values;
- requires a label and outcome;
- bounds all text lengths;
- assigns an application UUID;
- truncates excess options;
- creates safe generic options when too few were returned.

The first checkpoint has four model-generated research choices. Later
checkpoints have three research choices plus **Finish and synthesize**.

The finish choice is application-controlled. It uses the reserved focus
`__finish__`, so the model does not control the terminal state transition.

### 4.3 Pausing for human input

A checkpoint looks like:

```json
{
  "checkpoint_id": "uuid",
  "created_at": "UTC timestamp",
  "status": "pending",
  "options": [],
  "selection": null,
  "feedback": ""
}
```

The run becomes `waiting_for_human` and is saved before terminal input is
requested. This ordering makes the pause durable.

`main.py` calls `pending_checkpoint`, displays numbered choices, and asks for:

1. one choice number;
2. optional free-form guidance.

Only choices are shown. Raw evidence and sub-agent transcripts stay outside
the public interaction. If the user types `exit`, the checkpoint remains
pending and the CLI prints `/research-resume <run-id>`.

### 4.4 Recording the human decision

`select_research_direction` checks that a pending checkpoint exists and the
number is valid. It then:

1. Marks the checkpoint `selected`.
2. Stores the selected option ID.
3. Bounds and stores the feedback.
4. Appends the choice to `selected_directions`.
5. Sets `current_focus`.
6. Changes status to `running`.
7. Saves before external work starts.

The path is auditable:

```json
{
  "checkpoint_id": "uuid",
  "label": "Adoption barriers",
  "focus": "Research current adoption barriers.",
  "feedback": "Prioritize regulated industries.",
  "selected_at": "UTC timestamp"
}
```

If the focus is `__finish__`, the coordinator calls `_synthesize` without
launching another specialist.

### 4.5 Constructing a clean delegation

For an ordinary choice, the coordinator creates a focused task containing:

- selected direction;
- user feedback;
- requested results-per-search limit;
- instructions to report findings, uncertainty, and URLs.

It creates separate compact context containing only:

```json
{
  "original_query": "...",
  "research_brief": "...",
  "unresolved_questions": []
}
```

This serialized context is capped at 8,000 characters. The specialist does not
receive the full CLI history, all checkpoints, historical tool transcripts,
the compaction archive, or unrelated agent state.

This is the main-context-cleanliness pattern: durable state may be detailed,
but each worker receives only the information needed for its current task.

### 4.6 Running an isolated specialist

The coordinator uses `DEEP_RESEARCH_AGENT`. `dataclasses.replace` creates an
invocation-specific copy with the configured tool-round budget:

```python
replace(DEEP_RESEARCH_AGENT, max_tool_rounds=tool_rounds)
```

`run_specialist` creates a new Gemini client, new `agent_run_id`, fresh message
list, empty evidence list, and invocation-local event list. No prior specialist
conversation is reused.

The runtime manually controls function calling:

1. Send focused task and context.
2. Read requested function calls.
3. Check every tool against the agent allowlist.
4. Execute an allowed tool.
5. Convert tool exceptions into structured observations.
6. Save tool arguments and results as evidence.
7. Return the observation to the model.
8. Repeat within the configured tool-round budget.
9. Reserve a final tool-free call for concise synthesis.

The model may request a tool, but it cannot execute arbitrary Python. Python
code remains the authority boundary.

### 4.7 Gathering web evidence

`web_search` rejects empty queries, clamps result count to one through five,
calls the search provider, and returns:

```json
{
  "ok": true,
  "query": "focused query",
  "results": [
    {
      "title": "Page title",
      "url": "https://example.com/page",
      "snippet": "Search result summary"
    }
  ]
}
```

The specialist result contains:

- `answer`: concise natural-language findings;
- `evidence`: actual structured tool calls and results;
- `status`: completed, partial, or an error state;
- `_events`: model and tool telemetry.

The coordinator uses the answer for memory and structured evidence for source
provenance. Citations therefore do not depend only on model-written prose.

### 4.8 Source ledger and provenance

`_merge_sources` extracts results from specialist evidence. Every source keeps:

- URL;
- title;
- snippet;
- search query that found it;
- retrieval timestamp.

The URL is the deduplication key. A repeated URL is stored once. The ledger is
bounded by `DEEP_RESEARCH_SOURCE_LIMIT`. URLs, titles, snippets, and search
queries also have individual length bounds.

This ledger is used during future planning and final synthesis. It is the
system's durable answer to “where did this claim come from?”

### 4.9 Compact working memory

The specialist summary is saved with its direction, agent-run ID, status, and
timestamp. `_refresh_brief` then asks the model to update:

- `research_brief`: compressed factual understanding;
- `unresolved_questions`: up to ten remaining gaps.

The prompt requires disagreement and uncertainty to be preserved. The brief is
capped at 12,000 characters and each open question at 500 characters.

This brief is not hidden chain-of-thought. It is an inspectable application
artifact containing findings and unresolved issues.

### 4.10 Dynamic context compaction

Long-running agents cannot keep adding raw history forever. More context costs
more, slows calls, and can make important evidence harder to find.

`_active_context` defines the working set sent to coordinator model calls:

- original query;
- current focus;
- compact research brief;
- unresolved questions;
- selected direction path;
- three latest specialist summaries;
- source ledger.

`_estimate_tokens` serializes this state to UTF-8 and divides byte count by
four. This is a fast approximation, not the provider's exact tokenizer.

After every round, `_compact_if_needed` compares the estimate with
`DEEP_RESEARCH_COMPACTION_TOKENS`. If the threshold is crossed and older
summaries exist:

1. All summaries except the newest are copied to `compaction_history`.
2. The archive records a compaction UUID, timestamp, and prior estimate.
3. Active summaries are reduced to the newest one.
4. The factual brief is refreshed.
5. Active context is estimated again.
6. A compaction event is recorded.
7. State is saved immediately.

No history is deleted. Old detailed summaries move from working memory to
archival memory. The brief, open questions, user decisions, and source ledger
remain active.

This compaction is dynamic because context size triggers it. It is not tied to
a fixed round number.

### 4.11 Repeating the loop

After merging sources, updating memory, collecting events, and compacting,
`_make_checkpoint` runs again. The model sees updated evidence and can propose
new branches around discoveries, contradictions, gaps, or the user's past
feedback.

The CLI then displays three new research paths and one finish option.

### 4.12 Final synthesis

`_synthesize` sends active context to a tool-free model call. Its prompt
requires:

- a Markdown report;
- a direct answer to the original query;
- findings separated from uncertainty;
- a brief account of the user-directed path;
- citations using supplied source URLs;
- no invented facts or citations.

The report is stored in `final_report`, status becomes `completed`, a
completion event is appended, and the run is saved before the CLI prints it.

## 5. Tree-of-Thought pattern

Tree of Thought is implemented as an external, inspectable planning tree:

```text
Vague query
├── Direction A
├── Direction B  <-- human selects
├── Direction C
└── Direction D
        |
        v
  evidence updates state
        |
        ├── Direction B1
        ├── Direction B2  <-- human selects
        ├── Direction B3
        └── Finish
```

Each checkpoint is one tree level. Options are branches. The user is the branch
selector. Research on the chosen branch changes the state used to generate the
next level.

Compared with autonomous beam search:

- unselected branches are not executed;
- the user controls compute and direction;
- branches are regenerated dynamically;
- feedback can rewrite the intended focus;
- the chosen path is persisted.

The system does not request or expose private model chain-of-thought. It stores
only options, outcomes, focused tasks, evidence summaries, and user decisions.

The repository has a generic `tree_of_thought` helper in `src/reasoning.py`,
but this feature intentionally does not use it. That helper performs a bounded
autonomous search inside one workflow node. Deep research needs a multi-turn,
human-selected tree that can survive process restarts.

## 6. HITL and handoff patterns

The human gate is a directional handoff:

```text
Coordinator proposes paths
           |
           v
Human selects and edits intent
           |
           v
Coordinator delegates focused task
           |
           v
Specialist returns evidence and control
```

Properties of this HITL design:

- no branch is researched before approval;
- the user may add corrections;
- pending input is saved before the process waits;
- one selection resolves one checkpoint;
- the user explicitly decides when to finish;
- every choice and feedback message is auditable.

The specialist handoff is a bounded function call rather than the repository's
`HandoffManager`. Semantically it follows `return_to_coordinator`: the worker
performs a task, returns a result, and never owns the public conversation.

## 7. Persistent run state

Runs live at:

```text
data/research/<run-id>.json
```

| Field | Meaning |
|---|---|
| `run_id` | Stable UUID used for persistence and resume. |
| `status` | `created`, `running`, `waiting_for_human`, or `completed`. |
| `original_query` | Initial user question, always preserved. |
| `current_focus` | Most recently selected branch. |
| `research_brief` | Compact working understanding. |
| `unresolved_questions` | Gaps used to plan the next branches. |
| `selected_directions` | Ordered user-controlled tree path. |
| `source_ledger` | Deduplicated web provenance. |
| `subagent_summaries` | Recent summaries kept in active memory. |
| `checkpoints` | Every option set and decision. |
| `compaction_history` | Older summaries moved out of active context. |
| `token_estimate` | Approximate active-context size. |
| `final_report` | Completed Markdown report. |
| `events` | Lifecycle and specialist telemetry. |

`save_research_run` writes a temporary file and then replaces the destination,
reducing the chance of leaving half-written JSON.

`resume_deep_research` loads state without silently repeating research. The CLI
continues normally when the run is waiting at a pending checkpoint.

## 8. Configuration and bounds

| Setting | Default | Allowed range | Purpose |
|---|---:|---:|---|
| `DEEP_RESEARCH_COMPACTION_TOKENS` | 12,000 | 1,000–100,000 | Working-context threshold. |
| `DEEP_RESEARCH_MAX_ROUNDS` | 8 | 1–20 | Branches before forced synthesis. |
| `DEEP_RESEARCH_SUBAGENT_TOOL_ROUNDS` | 2 | 1–4 | Specialist evidence-gathering rounds. |
| `DEEP_RESEARCH_RESULTS_PER_SEARCH` | 5 | 1–5 | Requested results per search. |
| `DEEP_RESEARCH_SOURCE_LIMIT` | 80 | 10–300 | Maximum retained unique URLs. |
| `DEEP_RESEARCH_FALLBACK_MODEL` | `gemini-2.5-flash` | Model identifier | Fallback for deep-research model operations after two primary-model provider failures. |

`_integer_setting` restores defaults for invalid values and clamps valid
integers to safe ranges.

Other bounds include:

- initial query: 8,000 characters;
- initial focus: 2,000 characters;
- human feedback: 2,000 characters;
- option focus: 500 characters;
- specialist context: 8,000 characters;
- specialist summary and research brief: 12,000 characters each;
- ten unresolved questions;
- three recent summaries in active context.

### Deep-research model fallback

Deep-research operations start with `GEMINI_MODEL`, which remains Gemma by
default. Direction generation, compact-memory updates, final synthesis, and
the stateless research specialist all have fallback coverage. On the second
Gemma provider failure, the active call changes to
`DEEP_RESEARCH_FALLBACK_MODEL` and is retried.

The switch preserves gathered tool evidence, the focused task, and existing
model/tool events. Later model calls in that specialist invocation continue on
the fallback. The runtime records the actual model on `model_call` and
`model_error` events, while the live logger emits a `WARN` containing both
model identifiers. Other workflows and specialists continue using
`GEMINI_MODEL`; this fallback is deliberately limited to deep research.

## 9. Security and trust boundaries

Search text is untrusted. It may contain prompt-injection language such as
“ignore previous instructions.”

Defenses include:

- prompts label retrieved text as data, never instructions;
- specialists receive an explicit tool allowlist;
- Python validates each requested tool;
- automatic SDK function calling is disabled;
- web output is normalized into a small contract;
- all important fields and collections are bounded;
- final synthesis may cite only supplied URLs;
- Python, not the model, creates the reserved finish value;
- web text never becomes a system instruction.

These controls do not guarantee that every source claim is correct. Search
snippets may be stale or misleading, so the report must preserve uncertainty
and high-stakes conclusions still need human review.

## 10. Events and observability

Coordinator events include:

- `research_created`;
- `research_directions_proposed`;
- `research_context_compacted`;
- `research_completed`.

Specialist events include model calls, tool calls, arguments, results, latency,
provider errors, and token counters when available.

The research run ID becomes the specialist trace ID. The checkpoint ID becomes
the parent step ID. This connects every tool call to the exact human choice
that caused it.

## 11. Failure and fallback behavior

- Empty initial queries are rejected.
- Invalid model JSON becomes an empty object.
- Missing directions receive generic fallback options.
- Invalid choice numbers do not resolve the checkpoint.
- Unauthorized tools return structured errors.
- Tool exceptions become observations instead of crashing the loop.
- Empty specialist output is made explicit by the runtime.
- Empty final output becomes `No final report was produced.`
- CLI-level exceptions are printed as `Research failed: ...`.

State is saved before specialist execution. If the process dies during that
external call, the run can remain `running`. The current resume loop only
continues pending HITL sessions and does not replay an interrupted specialist,
which avoids accidental duplicate searches. Such a run currently requires
manual inspection or recovery.

## 12. Example lifecycle

```text
1. /research Is local-first software viable for enterprises?
2. Coordinator creates and saves a run.
3. Coordinator proposes four branches and pauses.
4. User selects "Security and compliance" with extra guidance.
5. Coordinator saves the choice.
6. Fresh specialist searches the web.
7. Coordinator deduplicates sources and updates compact memory.
8. Context is compacted if it crossed the threshold.
9. Coordinator proposes three new branches plus Finish.
10. Steps 4–9 repeat as directed by the user.
11. User selects Finish.
12. Coordinator saves and prints a cited Markdown report.
```

## 13. Architecture patterns incorporated

### Coordinator–specialist separation

The coordinator manages state, branching, HITL, and synthesis. Specialists
perform focused evidence gathering.

### Agent as a bounded tool

A focused task goes in; bounded evidence and a summary come back. `AgentSpec`
declares identity and authority.

### Stateless sub-agents

Every worker gets a fresh client and conversation, preventing context leakage
between branches.

### Human-guided Tree of Thought

The model proposes branches, the human selects one, and evidence changes the
next set of branches.

### Checkpoint and resume

State is persisted before waiting, so a process restart does not erase the
decision point.

### Working memory and archival memory

The brief, open questions, recent summaries, and sources are working memory.
`compaction_history` is durable detail outside the normal prompt.

### Dynamic compaction

Estimated prompt size, rather than round count, triggers compression.

### Structured control outputs

Direction and memory model calls use JSON so control flow does not depend on
parsing arbitrary prose.

### Source provenance

URLs come from structured tool evidence and are deduplicated before synthesis.

### Least authority

The specialist can call only tools declared in its allowlist.

### Bounded autonomy

Round, tool, result, source, field-size, and context budgets prevent runaway
research.

### Event-sourced audit trail

Events, checkpoints, choices, tool activity, and compactions preserve why the
current state exists.

### Explicit terminal states

`waiting_for_human` forbids autonomous continuation. `completed` means the
final report has been persisted.

## 14. Design tradeoffs

- Branches are sequential because every next step depends on user review.
- Unselected branches are not researched, saving cost and web calls.
- Token estimation is cheap and approximate.
- Search snippets provide provenance but are not full-page verification.
- Compaction archives summaries but keeps sources active for grounding.
- Plain dictionaries make JSON persistence transparent but provide less static
  type checking than typed models.
- Completion is human-controlled, with a round limit as a safety fallback.

## 15. Recommended reading order

1. The `/research` block in `main.py`.
2. `start_deep_research`.
3. `_make_checkpoint` and `_normalize_directions`.
4. `select_research_direction`.
5. `run_specialist` in `src/runtime.py`.
6. `web_search` in `src/tools.py`.
7. `_merge_sources` and `_refresh_brief`.
8. `_active_context` and `_compact_if_needed`.
9. `_synthesize`.
10. Research save/load functions in `src/store.py`.

This follows the same order in which information moves through the live
system.
