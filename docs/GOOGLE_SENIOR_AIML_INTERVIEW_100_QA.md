# Google Senior Software Engineer, AI/ML: 100 Interview Questions and Answers

This study bank is grounded in this repository’s framework-free multi-agent
system and tailored to the supplied Google AI Garage job description. Answers
combine project-specific implementation details with the production-scale
design decisions expected from a senior engineer.

## 1. Multi-Agent Architecture

### 1. What is a multi-agent system, and why use one instead of a single agent?

A multi-agent system separates responsibilities among agents with distinct
instructions, tools, permissions, and context. In this project, the coordinator
delegates live information to `research_agent`, repository inspection to
`codebase_agent`, computation to `data_science_agent`, and iterative research
to `deep_research_agent`.

The benefit is specialization and least authority. The research agent cannot
run arbitrary Python, while the data-science agent cannot browse the host
repository. Smaller prompts also reduce irrelevant context. The costs are
additional latency, more model calls, coordination errors, and harder
observability. A single agent is preferable for simple tasks; multiple agents
are justified when capabilities, security boundaries, or task dependencies are
meaningfully different.

### 2. Describe this project’s multi-agent architecture.

`main.py` starts either the local GUI or terminal mode. The GUI routes a query
to the general coordinator or deep-research state machine. The general
coordinator exposes agent-as-tool declarations such as
`ask_research_agent`. `run_turn` validates each delegation, launches accepted
specialists, gathers results, and performs final synthesis.

Agents are declared with `AgentSpec` in `src/agents.py`. Each spec defines a
name, description, system instructions, tool allowlist, tool-round limit,
output limit, and handoff routes. `src/runtime.py` creates a fresh client and
conversation for every specialist. Only the coordinator owns public
multi-turn memory.

### 3. Why are specialists stateless?

Stateless specialists avoid hidden coupling between requests. Each invocation
receives a focused task and bounded relevant context, so behavior is easier to
reproduce, secure, and evaluate. Parallel agents cannot accidentally inspect
one another’s unfinished reasoning.

Durable state belongs to the coordinator or workflow. If a specialist needs
prior information, it must be explicitly supplied. The trade-off is repeated
setup and possible loss of useful history. Production systems may add
retrieval-backed memory, but the retrieved state should still be explicit and
auditable.

### 4. How does the coordinator discover available agents?

`DELEGATION_TO_AGENT` maps model-visible function names to registered
`AgentSpec` names. `DELEGATION_DECLARATIONS` derives JSON function schemas from
that mapping and agent descriptions. The coordinator sees only these
high-level delegation tools, not every low-level capability.

This is safer than asking the model to invent agent names. Python validates the
requested function, invocation budget, duplicate use, task size, and agent
identity before execution.

### 5. How does hierarchical delegation work?

The coordinator is depth zero. A specialist invocation is depth one and is
linked through `parent_step_id` to a `delegation_start` event. The specialist
may call only its low-level tools. Its bounded result returns to the
coordinator, which can use it to plan a dependent delegation.

The project also has `HandoffManager`, which validates source-to-destination
routes, handoff depth, total transfer count, return policy, and explicit
accept/reject responses. The normal coordinator behaves like task-mode
delegation: control always returns to the parent.

### 6. How are independent and dependent agent tasks handled?

Independent function calls returned in one coordinator response are submitted
to a `ThreadPoolExecutor` and may run concurrently. Results are joined and
returned to the coordinator in original call order.

Dependent work uses a later coordinator round. For example, a weather agent
first gathers temperatures, then the coordinator passes those values to a
data-science agent to generate a chart. Starting both concurrently would be
incorrect because the chart input does not yet exist.

### 7. How would you prevent recursive agent explosion?

Use explicit budgets: maximum delegation rounds, total specialist
invocations, per-agent invocation limits, handoff depth, and handoff count.
This project enforces two coordinator delegation rounds, three specialist
invocations, one invocation per specialist per turn, and bounded specialist
tool rounds.

At scale I would also enforce tenant cost budgets, wall-clock deadlines,
cancellation propagation, maximum fan-out, circuit breakers, and policy checks
before creating remote tasks.

### 8. What is the difference between an agent-as-tool and a conversational handoff?

An agent-as-tool receives a bounded task, returns a result, and never owns the
user conversation. A conversational handoff transfers active ownership; the
new agent may continue interacting with the user until it transfers control
again.

This project primarily uses agent-as-tool semantics. That is appropriate for
specialists because the coordinator preserves a consistent user experience
and central policy boundary.

### 9. How would you choose agent boundaries?

Boundaries should follow distinct capabilities, data access, security
authority, scaling characteristics, or evaluation rubrics. Weather retrieval,
code search, and sandboxed computation deserve different agents because their
tools and risks differ.

Avoid creating agents only to mirror organizational teams. Excessive
fragmentation increases prompt overhead and coordination failures. A useful
test is whether the new agent needs a materially different tool allowlist,
context, model, SLA, or quality metric.

### 10. How would you migrate this framework-free system to Google ADK?

Represent specialists as ADK agents or `AgentTool`s, expose a `root_agent`,
move conversation state into ADK sessions, and emit native ADK events. The
custom deep-research loop could become a custom `BaseAgent` or graph workflow.

I would migrate incrementally: first wrap existing functions as ADK tools for
Developer UI visibility, then replace specialist execution with native
sub-agents, and finally map persistence and HITL to ADK session/resume
mechanisms. Existing JSON state should remain authoritative until parity is
verified.

## 2. Dynamic Planning and Collaboration

### 11. What makes planning in this project dynamic?

The complete task graph is not hard-coded. The coordinator model sees the user
request and available specialist descriptions, then chooses agents and focused
tasks. After receiving evidence, it can create a dependent task in another
round.

Python controls the boundaries; the model controls decomposition within those
boundaries. This hybrid approach combines adaptability with deterministic
limits.

### 12. What is plan-and-solve?

Plan-and-solve separates decomposition from execution. The model first
identifies subproblems, dependencies, and expected outputs, then executes or
delegates them and finally synthesizes the result.

For production use, plans should be structured objects with IDs, dependencies,
status, budgets, and acceptance criteria. Free-form plans are difficult to
validate, resume, or observe.

### 13. How do you represent task dependencies?

The general coordinator uses sequential delegation rounds for implicit
dependencies. The workflow subsystem uses an explicit DAG: every node has an
ID, type, configuration, and `depends_on` list.

Explicit dependencies are preferable for repeatable business processes;
LLM-driven rounds are preferable when decomposition cannot be known in
advance.

### 14. How should agents collaborate without sharing an oversized context?

Agents should exchange bounded contracts: task, compact context, evidence,
artifacts, status, and uncertainty. Raw private histories should stay local to
the worker.

This project sends specialists a task plus at most a small context string.
Deep research sends the original query, compact brief, and unresolved
questions—not the full checkpoint and tool transcript.

### 15. How would you detect a poor plan before executing it?

Validate structural properties and semantic properties. Structural checks
include unknown agents, cycles, missing dependencies, duplicate work, and
budget violations. Semantic checks include whether every user requirement is
covered, required evidence has a producer, and consequential actions have a
human gate.

A planner-critic pattern can score coverage, feasibility, security, cost, and
expected information gain before execution.

### 16. What is hierarchical planning?

Hierarchical planning decomposes a high-level objective into increasingly
concrete tasks. A coordinator chooses domains; specialists choose tool calls;
tools perform deterministic operations.

The hierarchy limits prompt complexity and authority. At Google scale, each
level could have separate SLOs, queues, and ownership while sharing trace and
correlation IDs.

### 17. How do you manage collaboration failures?

Return typed results rather than throwing every error into the parent.
Specialists in this project return statuses such as `completed`, `partial`,
`provider_error`, and `step_limit_reached`. The coordinator can disclose
partial evidence or choose another step.

Production policies should distinguish retryable provider errors, invalid
plans, tool failures, policy denials, and permanent business failures.

### 18. How would you support cancellation?

Every run needs a cancellation token propagated from HTTP request to
coordinator, worker futures, model calls, and tools. Long-running tools should
periodically check cancellation. Remote tasks need idempotency keys and a
cancel endpoint.

The current daemon threads do not provide full cancellation; adding
cooperative cancellation would be required before production deployment.

### 19. How would you make planning deterministic enough for enterprise use?

Keep business invariants in code: allowlists, schemas, DAG validation,
approval gates, budgets, and idempotency. Let the model select among permitted
actions, not redefine the policy.

Use low-temperature structured outputs, version prompts and schemas, record
the plan, and replay evaluations against every change.

### 20. How would you prioritize research directions?

Score relevance, uncertainty reduction, evidence gaps, novelty, risk, cost,
and dependency value. Deep research asks the model for distinct directions
that improve coverage or challenge assumptions, while a human selects the
branch.

A more rigorous version would expose scores and use expected information gain
divided by estimated cost.

## 3. ReAct, Chain of Thought, Tree of Thought, and Reflection

### 21. Explain the ReAct pattern.

ReAct alternates model decisions and actions: inspect the task, request a tool,
observe the result, and continue until a final answer. `run_specialist`
implements this loop manually with native function calls and structured tool
responses.

The application records inspectable decisions and observations, not private
chain-of-thought. This distinction matters for safety and observability.

### 22. Walk through this project’s ReAct loop.

The runtime builds a tool-enabled model configuration, calls the model, reads
function calls, validates each tool against the agent allowlist, executes the
tool, stores evidence, and returns a `FunctionResponse`. A final tool-free call
forces concise synthesis after the tool budget.

The loop is bounded by `max_tool_rounds + 1`, preventing endless
tool-selection cycles.

### 23. What is Chain of Thought, and should it be logged?

Chain of Thought is intermediate reasoning generated by a model. Applications
should not depend on or expose hidden reasoning. Instead, ask for concise
decision summaries, evidence, confidence, and outcomes.

This repository’s `reasoning_chain` stores application-level decision records,
explicitly not private model reasoning.

### 24. What is Tree of Thought?

Tree of Thought explores several candidate approaches rather than following
one greedy path. Candidates may be generated, evaluated, pruned, and expanded
over multiple levels.

The generic `tree_of_thought` helper performs bounded autonomous search. Deep
research implements a persistent human-guided tree: the model proposes four
branches, the user selects one, evidence updates state, and the next level is
generated.

### 25. Why use human-guided Tree of Thought?

It spends compute only on approved directions and incorporates domain
judgment. This is useful when research goals are ambiguous or consequential.

The trade-off is user latency. Autonomous beam search is faster for low-risk
tasks, while HITL is better where direction quality matters more than
throughput.

### 26. What is self-reflection?

Self-reflection asks an agent or critic to inspect an answer or plan for
missing evidence, contradictions, unsafe actions, or poor coverage before
finalizing.

It can improve quality but also adds cost and may reinforce the same model’s
biases. Use independent evidence, explicit rubrics, or a different critic model
where possible.

### 27. How would you add reflection to this project?

After specialist results but before final synthesis, call a critic with the
user requirements, evidence ledger, and draft answer. Require structured
issues: unsupported claim, missing requirement, contradiction, or safety
concern.

The coordinator should repair only validated issues and enforce a strict
reflection-round limit.

### 28. Compare ReAct and plan-and-solve.

ReAct plans locally after each observation and is adaptive. Plan-and-solve
creates a broader decomposition first and can execute independent tasks in
parallel.

A strong system combines them: a planner builds tasks, while each specialist
uses ReAct to execute its task.

### 29. What is reasoning-model fallback?

Fallback changes the model after defined failures while preserving the task
state and evidence. Deep research begins with Gemma and switches to
`gemini-2.5-flash` after two provider failures.

Events record the actual model. Fallback should be scoped, observable, and
tested because models may differ in function-calling behavior and output
schemas.

### 30. How do you evaluate advanced reasoning?

Measure final task success, plan coverage, tool correctness, evidence
grounding, branch efficiency, recovery from failures, and cost—not the
plausibility of verbose reasoning text.

Use adversarial tasks with dependencies, distractors, ambiguous goals, and
tool failures. Compare trajectories, not only final wording.

## 4. DAGs and Workflow Runtime

### 31. What is a DAG, and why is it useful for agents?

A directed acyclic graph represents tasks and dependencies without cycles. A
node runs only after its prerequisites complete.

DAGs provide deterministic orchestration, parallelism, retry boundaries, and
resume points. They suit HR workflows such as ingest → security scan → score →
register → approve → outreach.

### 32. How does this project validate workflows?

The workflow validator checks duplicate IDs, missing dependencies, cycles,
unknown agents or tools, graph size, and depth. Validation happens before side
effects.

This converts many runtime failures into clear configuration errors.

### 33. How does the scheduler find runnable nodes?

It selects unfinished nodes whose dependencies all have `COMPLETED` results.
Human-approval nodes create pending approval records; other ready nodes execute
in a bounded thread pool.

Failed dependencies cause descendants to become `BLOCKED`, preventing invalid
downstream execution.

### 34. How is HITL represented in a DAG?

A human node stores an `ApprovalRequest` and a `WAITING_FOR_HUMAN` node result.
The workflow status changes to `waiting_for_human`.

Resolution updates the approval, replaces the node result with completed or
failed state, and resumes scheduling.

### 35. How would you add retries to DAG nodes?

Store attempt count, retry policy, last error, and next eligible timestamp.
Retry only classified transient failures with exponential backoff and jitter.

Side-effecting nodes require idempotency keys; otherwise retries can duplicate
emails or writes.

### 36. How do DAGs differ from agent loops?

DAGs make topology explicit and are easier to reason about, while agent loops
choose steps dynamically. DAGs are strong for regulated processes; loops are
strong for open-ended investigation.

Hybrid systems let an agent generate or modify a validated DAG.

### 37. How would you support dynamic DAG expansion?

Allow a planner node to emit typed node definitions. Validate new nodes,
dependencies, budgets, and permissions before merging them into the graph.

Persist the graph version and expansion event so resume and audit use the exact
topology that executed.

### 38. How do you prevent cycles in a dynamic graph?

Run topological validation after every expansion. A proposed edge from node A
to B is invalid if B already reaches A.

For large graphs, maintain indegrees or incremental reachability indexes rather
than recomputing everything.

### 39. What should a node result contain?

Status, structured output, error classification, attempt count, start/end
timestamps, artifacts, trace IDs, model/tool usage, and policy decisions.

Avoid passing unbounded raw output downstream; persist it as an artifact and
pass a typed reference.

### 40. How would you scale DAG execution?

Move ready nodes to durable queues, use stateless workers, lease tasks, persist
state transactionally, and enforce idempotency. Partition by tenant and apply
per-tenant quotas.

Use a scheduler service for dependencies and workers specialized by capability
or security boundary.

## 5. Dynamic Context Compaction and Memory

### 41. What is dynamic context compaction?

It reduces active model context when estimated size crosses a threshold while
preserving essential semantics and archival history. It is dynamic because
content size—not a fixed turn count—triggers it.

The deep-research agent estimates serialized active-context bytes divided by
four and compares that with `DEEP_RESEARCH_COMPACTION_TOKENS`.

### 42. What is active context in this project?

It contains original query, current focus, compact brief, unresolved questions,
selected directions, the latest three specialist summaries, and the source
ledger.

Checkpoint history, old events, and compaction archives remain persisted but
outside normal prompts.

### 43. What happens when compaction triggers?

All specialist summaries except the newest move into a
`compaction_history` record with a compaction ID, timestamp, and pre-compaction
estimate. Active summaries are reduced to one, the research brief is refreshed,
the estimate is recalculated, an event is emitted, and state is saved.

The process moves information between memory tiers; it does not delete audit
history.

### 44. Why not simply truncate old messages?

Truncation is cheap but can remove original requirements, decisions, failed
approaches, or safety constraints. Semantic compaction attempts to preserve
those milestones.

A production harness often combines immutable critical fields, recent raw
messages, structured state, and retrievable archives.

### 45. What is recursive summarization?

It repeatedly combines the previous brief with new evidence to produce a new
brief. This enables long sessions but may accumulate semantic drift.

Mitigate drift by preserving structured constraints, sources, contradictions,
and artifacts independently from prose summaries.

### 46. How do you measure semantic loss?

Replay tasks that require facts from pre-compaction history and compare answer
accuracy before and after compaction. Measure constraint retention, retrieval
recall, numeric fidelity, source preservation, and downstream task success.

Also inspect compression ratio, added latency, and tokens saved over subsequent
calls.

### 47. What should never be compacted away?

System policy, original user goal, explicit constraints, approvals, current
task state, unresolved failures, source provenance, artifact IDs, and decisions
required by downstream work.

These may be represented compactly but must remain semantically intact.

### 48. How would you improve the current token estimate?

Use the actual tokenizer for the selected model and include system prompts,
tool schemas, and response reserves. Maintain separate budgets for input and
expected output.

The current byte/4 estimate is fast and provider-independent but approximate.

### 49. How would retrieval-backed memory complement compaction?

Archive raw events and summaries in searchable storage. Before each task,
retrieve memories relevant to the current focus and inject only top-ranked
items.

This reduces prompt size but introduces embedding, ranking, freshness, and
access-control risks.

### 50. When is compaction not worth doing?

If the session is about to finish, the extra summarization call may cost more
than future savings. It is also unnecessary when context remains far below the
threshold.

Use expected remaining turns and projected token savings to decide whether to
compact.

## 6. Observability, Trajectories, Phoenix, and Evals

### 51. What is an agent trajectory?

A trajectory is the ordered execution record from user request through model
decisions, delegations, tool calls, errors, and final answer.

This project records `trace_id`, `step_id`, `parent_step_id`, agent identity,
tokens, latency, arguments, results, and status.

### 52. Why are parent IDs important?

They reconstruct hierarchy. A specialist model or tool event points to the
delegation that caused it, allowing a trace UI to show nested operations.

Without parent IDs, concurrent events become a flat, ambiguous log.

### 53. What does Phoenix provide?

Phoenix receives OpenTelemetry-style spans and supports trace inspection,
latency analysis, token tracking, and offline evaluation. The JSONL trajectory
remains the local source of truth, so Phoenix is optional rather than a runtime
dependency.

This separation prevents observability outages from breaking user requests.

### 54. Compare Phoenix and LangSmith.

Both trace agent and LLM operations and support evaluations. LangSmith is
tightly integrated with LangChain/LangGraph and provides managed datasets,
experiments, and tracing. Phoenix is OpenTelemetry-friendly and strong for
open-source observability and model evaluation.

Selection should consider data residency, ecosystem, cost, deployment model,
and enterprise governance.

### 55. Which metrics matter for agents?

Task success, groundedness, tool correctness, plan efficiency, agent selection,
handoff success, policy violations, retries, token cost, end-to-end latency,
and artifact validity.

Report p50, p95, and p99, not only averages. Segment by intent, model, tool,
tenant, and failure category.

### 56. How would you evaluate agent routing?

Build a labeled dataset mapping requests to acceptable agents or direct
answers. Score route accuracy, unnecessary delegation rate, missed
specialization, latency, and cost.

Some requests allow multiple valid routes, so labels may be sets or rubric
scores rather than one class.

### 57. How would you evaluate trajectories automatically?

Use deterministic checks first: allowed tools, required evidence, no duplicate
side effects, valid artifacts, and bounded steps. Then use an LLM judge for
coverage, groundedness, or clarity.

Calibrate the judge against human labels and monitor judge drift.

### 58. How do you attribute cost?

Record token usage and model name per call, tool runtime, artifact storage, and
external API charges. Aggregate by trace, agent, task type, tenant, and model.

Fallback calls and failed attempts must be counted; otherwise reliability
problems appear free.

### 59. How would you detect regressions?

Version prompts, models, tools, schemas, and policies. Replay a stable dataset
and compare quality, latency, cost, route distribution, failures, and safety
metrics against a baseline.

Use confidence intervals and block rollout on statistically or operationally
significant regressions.

### 60. What information should not be logged?

Secrets, OAuth tokens, unnecessary personal data, complete confidential
documents, hidden reasoning, and unrestricted tool outputs.

Use redaction, field allowlists, retention limits, tenant isolation, and
role-based access to traces.

## 7. Safety, Compliance, HITL, and Sandboxing

### 61. What is indirect prompt injection?

It is malicious instruction text embedded in retrieved data such as web pages,
documents, resumes, or emails. The agent may mistake data for instructions.

Prompts must label retrieved content as untrusted, and application policy must
control tools regardless of model text.

### 62. How does this project mitigate prompt injection?

Specialist prompts treat retrieved content as data, tool allowlists constrain
authority, code validates every tool request, and text/outputs are bounded.
Resume processing has an isolated security classifier and manual override flow.

No single prompt is sufficient; mitigation is layered.

### 63. Explain least authority for agents.

Every agent receives only the tools and data required for its role. The
research agent cannot execute Python; the codebase agent is read-only and
cannot inspect secret paths; the email agent cannot invent content.

Least authority limits damage when a model is confused or injected.

### 64. How is generated code sandboxed?

`run_python` executes code in an ephemeral Docker container with no network,
read-only filesystem, dropped Linux capabilities, no-new-privileges, resource
limits, timeout, and one writable output mount.

The container receives no host repository or credentials. Artifact paths are
validated before returning them.

### 65. Why is Docker alone not a complete security boundary?

Container runtimes can have vulnerabilities, host configuration can be unsafe,
and resource limits can be incomplete. Production high-risk code execution may
require microVMs, gVisor, seccomp, isolated nodes, egress proxies, and strong
image provenance.

Treat sandboxing as defense in depth.

### 66. Where should HITL be required?

Before irreversible or high-impact actions: sending email, changing employee
records, granting access, making employment decisions, or exposing sensitive
data.

Research direction selection is a softer HITL gate; email delivery is a hard
authorization boundary.

### 67. How does immutable approval work?

Persist the exact draft, calculate a content hash, and bind approval ID to that
hash. Immediately before sending, recompute and compare. Any edit invalidates
approval.

This prevents approving one message and sending another.

### 68. How would you protect HR data?

Apply purpose limitation, data minimization, encryption, regional controls,
short retention, fine-grained authorization, audit logs, and separation of
identity from scoring evidence.

Never use protected attributes or inferred proxies in employment decisions.
Provide review and appeal mechanisms.

### 69. How do you secure web tools?

Restrict protocols and destinations, prevent access to metadata/private
networks, bound response size and time, normalize output, scan content, and
store provenance.

The current search tool returns small title/URL/snippet records rather than
arbitrary page execution.

### 70. What is the role of policy versus prompts?

Prompts guide probabilistic behavior. Policy code deterministically decides
what is allowed.

Consequential security must live in code: permissions, hashes, schemas,
allowlists, approvals, and idempotency.

## 8. LLM Training, LoRA, SFT, NLP, and Reasoning

### 71. What is supervised fine-tuning?

SFT trains a pretrained model on labeled input-output examples using standard
next-token loss. It teaches domain behavior, formats, terminology, or task
patterns.

For agent systems, examples should include tool selection and structured
outputs, but tool authorization must still remain outside the model.

### 72. What is LoRA?

Low-Rank Adaptation freezes base-model weights and trains small low-rank
matrices inserted into selected layers. It greatly reduces trainable
parameters, memory, and storage.

LoRA is useful for domain adaptation or per-tenant variants, but serving many
adapters adds routing, compatibility, and evaluation complexity.

### 73. When would you choose prompting, RAG, SFT, or LoRA?

Use prompting for behavior that can be described reliably, RAG for changing
knowledge, and SFT/LoRA for repeated behavioral or domain-pattern gaps that
prompting cannot solve economically.

Do not fine-tune to memorize frequently changing HR policy; retrieve the
current authoritative policy instead.

### 74. How do you prepare SFT data for an agent?

Collect high-quality trajectories, remove secrets and private reasoning,
normalize tool schemas, label correct decisions, include refusals and recovery,
and deduplicate examples.

Split by task families to prevent near-duplicate leakage between training and
evaluation.

### 75. How would you fine-tune reasoning behavior safely?

Train on verifiable outcomes, concise rationale summaries, tool evidence, and
structured plans rather than requiring disclosure of private reasoning.

Use process supervision where labels are reliable, and validate against
shortcut learning.

### 76. What are common NLP challenges in HR systems?

Ambiguous job terminology, multilingual text, abbreviations, sparse evidence,
document noise, protected attributes, and historical bias.

Use grounded extraction, explicit missing-value handling, calibrated
confidence, and human review for uncertain decisions.

### 77. What is embedding-based retrieval?

Text is mapped to dense vectors; similarity retrieves semantically related
chunks. It improves recall beyond lexical matching.

Production retrieval also needs metadata filters, permissions, hybrid lexical
search, reranking, freshness, and evaluation.

### 78. What is catastrophic forgetting?

Fine-tuning on a narrow dataset can degrade general capabilities. Mitigate with
mixed-domain data, lower learning rates, regularization, adapters, and broad
regression evaluations.

LoRA reduces weight modification but does not eliminate behavioral regression.

### 79. How do you evaluate a fine-tuned model?

Compare against the base model on target quality, general capabilities,
safety, calibration, tool use, latency, and cost. Use held-out and adversarial
sets.

Evaluate the complete agent harness, not only isolated model responses.

### 80. How would model distillation help?

A strong teacher generates or labels examples for a smaller student. The
student can reduce latency and cost for routing, extraction, or classification.

Teacher errors must be filtered, and the student should defer low-confidence
cases to a stronger model.

## 9. ML Fundamentals: Bias, Variance, and Generalization

### 81. Explain the bias-variance trade-off.

High-bias models are too simple and underfit; high-variance models fit training
noise and overfit. Generalization error reflects both plus irreducible noise.

The best complexity depends on dataset size, noise, feature quality, and
regularization.

### 82. How do you diagnose underfitting?

Training and validation performance are both poor and similar. Inspect learning
curves, residuals, feature coverage, and model capacity.

Fix with better features, greater capacity, longer training, reduced
regularization, or improved labels.

### 83. How do you diagnose overfitting?

Training performance is strong while validation degrades. Symptoms include
unstable results across folds and sensitivity to small changes.

Fix with more data, augmentation, regularization, early stopping, simpler
models, leakage removal, or better cross-validation.

### 84. What is data leakage?

Leakage occurs when training features contain information unavailable at
prediction time or derived from the label. It produces unrealistically high
offline metrics.

For HR data, random splitting may leak the same employee, manager, or template
across sets. Use entity- and time-aware splits.

### 85. Why is class imbalance important?

Accuracy can hide failure on rare but important classes. Use precision,
recall, PR-AUC, class-specific calibration, and cost-sensitive thresholds.

In HR applications, analyze performance across relevant groups while
respecting privacy and legal requirements.

### 86. What is calibration?

A calibrated model’s predicted probability matches empirical frequency. It
matters when confidence drives routing or human review.

Measure reliability curves and expected calibration error; improve with
temperature scaling or isotonic regression.

### 87. How do agent evaluations overfit?

Prompts and routers may be repeatedly tuned against a small fixed evaluation
set, effectively memorizing it. LLM judges can also favor particular styles.

Maintain hidden holdouts, rotate adversarial sets, and monitor live
distribution shifts.

### 88. What is distribution shift?

Production input differs from training or evaluation data. HR policy changes,
new roles, languages, and organizational changes can shift distributions.

Monitor feature, intent, outcome, and calibration drift, and create safe
fallbacks.

### 89. How do you select a decision threshold?

Use business costs of false positives and negatives, capacity for human
review, calibration, and legal constraints. Do not default mechanically to
0.5.

Thresholds should be versioned and evaluated by segment.

### 90. How do you balance model quality and system quality?

Model benchmarks are only one component. Retrieval, prompts, tools, policy,
latency, uptime, and UX determine end-to-end value.

A slightly weaker model in a well-grounded, observable harness may outperform
a stronger model with poor context and unsafe tools.

## 10. Scale, Enterprise Delivery, and Leadership

### 91. How would you scale this project to millions of users?

Separate UI/API, orchestration, workers, state store, artifact store, and
telemetry. Replace in-memory operations and local JSON with durable queues and
transactional storage. Run stateless workers behind autoscaling.

Apply tenant quotas, caching, backpressure, regional deployment, and graceful
degradation.

### 92. How would you design persistence?

Use relational storage for runs, checkpoints, approvals, tasks, and
idempotency; object storage for large evidence and artifacts; and an analytics
store for traces.

Use optimistic concurrency or transactions to prevent two workers resolving
the same checkpoint.

### 93. How do you design for multi-tenancy?

Attach tenant identity to every session, task, artifact, trace, and query.
Enforce authorization in storage and tool layers, not only prompts.

Use quotas, encryption boundaries, tenant-aware caching, and noisy-neighbor
controls.

### 94. What reliability patterns apply to model APIs?

Timeouts, bounded retries with exponential backoff and jitter, model fallback,
circuit breakers, bulkheads, rate limiting, and idempotent request handling.

Record failed attempts and fallback models so reliability cost remains visible.

### 95. How would you roll out an agentic HR feature?

Start with offline evaluation, shadow traffic, and advisory outputs. Progress
to small opt-in pilots with human review, then expand by risk tier.

Use feature flags, rollback, audit review, legal/privacy sign-off, and explicit
success and harm metrics.

### 96. How do you define SLOs for agents?

Define availability, p95 latency, task success, groundedness, tool error rate,
policy violations, and cost per successful task.

Different routes need different SLOs; deep research may take minutes while a
simple HR lookup should respond quickly.

### 97. How do you handle a latency incident?

Use traces to separate routing, queue, model, tool, retry, and synthesis time.
Check provider errors, token growth, fan-out, and external dependencies.

Mitigate through circuit breakers, smaller models, cached evidence, reduced
context, or temporary route disablement.

### 98. How would you provide technical leadership?

Clarify invariants and risks, write design documents, create measurable
quality gates, mentor engineers, and make trade-offs explicit. For incidents,
coordinate mitigation and lead blameless root-cause analysis.

Leadership also means saying no to autonomy where evidence, permissions, or
review are insufficient.

### 99. How would you collaborate with PMs and AI strategists?

Translate goals into user journeys, risk tiers, measurable outcomes, and
technical constraints. Prototype quickly, but distinguish demo quality from
production readiness.

Communicate uncertainty, model limitations, operating cost, and governance in
plain language.

### 100. How would you present this project in the interview?

Describe it as a framework-free learning system that exposes every agent
boundary: dynamic routing, stateless specialists, ReAct tool loops, dependent
delegation, DAG workflows, HITL approvals, human-guided Tree of Thought,
dynamic compaction, model fallback, sandboxed Python, prompt-injection
mitigation, trajectory logging, Phoenix export, and a live observability UI.

Then discuss production gaps honestly: local JSON, in-memory operations,
limited cancellation, sequential tools, approximate token estimation, and a
development-only UI. Propose durable queues, transactional state,
tenant-aware security, native ADK events, stronger evaluations, and staged HR
rollout. Senior-level strength comes from explaining both what was built and
how it would safely scale.

## Final Preparation Advice

For each answer, practice three layers:

1. A 30-second definition.
2. A project-specific implementation example.
3. A production-scale trade-off or improvement.

Use concrete trace evidence—agent names, event fields, budgets, fallback
behavior, and sandbox controls—rather than only framework vocabulary.
