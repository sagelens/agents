# Concepts

## Language model versus agent

A language model maps input tokens to output tokens. It cannot independently
fetch today's weather or browse a live website. An agent is an application
loop around a model. The model chooses an action, ordinary Python executes it,
and the result is returned to the model.

The model is therefore the decision-maker, while the Python runtime is the
authority. The runtime validates tool names, controls network access, measures
calls, and decides when execution must stop.

## Multi-agent system

A multi-agent system composes several separately prompted model invocations.
This project uses one coordinator and three stateless specialists. Each
specialist has a narrow tool allowlist, while the coordinator owns public
memory, delegation, and final synthesis.

“Agent” does not imply a separate process or model provider. Each specialist
is a fresh Gemma invocation with a distinct role, context boundary, tools,
result contract, and trace identity.

## ReAct

ReAct means interleaving reasoning and acting:

1. The model receives the goal and available tool schemas.
2. It returns either a final answer or a structured function call.
3. Python executes that function.
4. The tool result becomes an observation.
5. The model receives the observation and makes its next decision.

Our stored trace records decisions such as `call_tool` and `final_answer`, but
does not store private chain-of-thought.

## Chain of Thought

Chain of Thought (CoT) is a single sequence of intermediate reasoning steps.
It can help on multi-step problems, but a generated explanation is not
guaranteed to be a faithful description of the model's internal computation.
For product observability, record external actions and concise summaries rather
than requesting or persisting private hidden reasoning.

## Tree of Thoughts

Tree of Thoughts (ToT) generates multiple possible next steps, scores them, and
may backtrack. It can help with puzzles and search problems but multiplies model
calls, latency, and token cost. Two simple information tools do not justify it
in version one.

## DAG decisions

A directed acyclic graph contains nodes connected by one-way edges and has no
cycle. Nodes can represent tasks, while edges represent dependencies:

```text
user question
   |----> weather lookup ----|
   |                         |----> synthesize answer
   |----> web search --------|
```

Independent nodes may run in parallel. Acyclicity guarantees that dependency
execution cannot loop forever. A DAG is a controlled workflow; ReAct is a
dynamic loop in which the model chooses the next action. They can be combined
later by letting an agent create a validated DAG that the runtime executes.

## Conversation, session, turn, trace, and step

- A message is one user, assistant, or tool item.
- A session is one multi-turn conversation.
- A turn begins with one user message and ends with one assistant answer.
- A trace is the execution record for that turn.
- A step is one model call or tool call inside that trace.

`session_id`, `turn_id`, `trace_id`, and `step_id` therefore have different
lifetimes and should not be collapsed into one identifier.

## Trajectory

A trajectory is the ordered path taken during a turn. It includes model
decisions, tool calls, observations, errors, token counts, latencies, and the
termination state. It answers “how did the agent arrive here?” rather than only
“what answer did it return?”

## Tokens

Models process tokens rather than words. Input tokens include instructions,
history, tool schemas, and observations. Output tokens include model-generated
content. Thinking-capable models may report thinking tokens separately. This
agent reads the provider's `usage_metadata`; it does not estimate usage.

## Latency

- Model latency measures one Gemini request.
- Tool latency measures one Python tool execution and its network calls.
- Turn latency measures the entire request, including every model and tool step.

The total can exceed the sum of rounded steps because orchestration and storage
also consume time.

## Tool calling is not tool execution

Gemma returns a structured request containing a tool name and arguments. It
does not run Python. Our allowlist maps that name to a known function. Never
use `eval`, a shell command, or dynamic imports on model-generated text.

## Code retrieval and grounding

The model does not automatically know the current contents of a local
repository. `grep_code` is a retrieval tool: it finds small pieces of relevant
source code and returns their file paths, line numbers, and nearby context.
Gemma can repeat the search to follow a symbol from its use to its definition.

Grounding means basing the answer on this retrieved evidence instead of model
memory. Grep is intentionally narrower than reading every file: smaller
observations reduce token use, latency, irrelevant context, and secret exposure.

The search pattern may be a regular expression. Fixed-string mode should be
used for symbols containing punctuation or whenever regex behavior is
unnecessary.

## Generated code and sandbox execution

`run_python` is a code-execution tool. Gemma writes a complete short program,
but the host application—not the model—decides how it runs. The application
passes source through standard input to a fresh Docker container and returns
observable output.

The sandbox image contains NumPy, pandas, SciPy, matplotlib, seaborn, and
scikit-learn. Matplotlib uses the non-interactive `Agg` backend, so generated
charts must be saved beneath `/output` instead of displayed with `plt.show()`.

Docker isolation reduces risk but is not a perfect security boundary. This
design is intended for a local single-user learning agent rather than hostile
multi-tenant code execution.

## Sources

- [ReAct paper](https://arxiv.org/abs/2210.03629)
- [Chain-of-Thought paper](https://proceedings.neurips.cc/paper_files/paper/2022/hash/9d5609613524ecf4f15af0f7b31abca4-Abstract-Conference.html)
- [Tree of Thoughts paper](https://arxiv.org/abs/2305.10601)
- [Graph of Thoughts paper](https://ojs.aaai.org/index.php/AAAI/article/download/29720/31236)
- [Google: Run Gemma with the Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)
