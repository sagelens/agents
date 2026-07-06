"""Integration layer joining workflows to existing specialists and Gemini."""

import json
from typing import Callable
from uuid import uuid4

from google import genai
from google.genai import types

from .agents import AGENT_SPECS
from .handoffs import HandoffManager, HandoffRequest
from .protocol import AgentMessage, MessageBus, MessageType
from .reasoning import reasoning_chain, tree_of_thought
from .runtime import run_specialist
from .store import save_workflow_run
from .tools import TOOL_FUNCTIONS
from .workflow import Workflow
from .workflow_runtime import WorkflowRuntime


def _json_model(client, model: str, instruction: str, payload: dict) -> dict:
    """Ask for one JSON object and safely normalize malformed output."""
    response = client.models.generate_content(
        model=model,
        contents=json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=instruction,
            response_mime_type="application/json",
        ),
    )
    try:
        return json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return {"summary": response.text or "", "decision": response.text or ""}


def build_workflow_runtime(api_key: str, model: str, trace_id: str):
    """Create a workflow runtime whose handlers use existing project boundaries."""
    client = genai.Client(api_key=api_key)
    collected_events: list[dict] = []

    def agent_handler(agent_name: str):
        def execute(task: str, inputs: dict) -> dict:
            result = run_specialist(
                api_key=api_key,
                model=model,
                spec=AGENT_SPECS[agent_name],
                task=task,
                context=json.dumps(inputs, ensure_ascii=False)[:8_000],
                trace_id=trace_id,
                parent_step_id=str(uuid4()),
            )
            collected_events.extend(result.pop("_events", []))
            return result

        return execute

    agent_handlers = {name: agent_handler(name) for name in AGENT_SPECS}
    bus = MessageBus()

    def protocol_handler(agent_name: str):
        def receive(message: AgentMessage) -> AgentMessage:
            if message.type == MessageType.HANDOFF_REQUEST:
                payload = message.payload
                result = agent_handlers[agent_name](
                    str(payload.get("task", "")),
                    {"handoff_context": payload.get("context_summary", "")},
                )
                return AgentMessage(
                    sender=agent_name,
                    recipient=message.sender,
                    type=MessageType.HANDOFF_ACCEPT,
                    payload={"result": result},
                    trace_id=message.trace_id,
                    correlation_id=message.correlation_id,
                    reply_to=message.message_id,
                )
            return AgentMessage(
                sender=agent_name,
                recipient=message.sender,
                type=MessageType.ERROR,
                payload={"error": f"Unsupported message type: {message.type.value}"},
                trace_id=message.trace_id,
                correlation_id=message.correlation_id,
                reply_to=message.message_id,
            )

        return receive

    for name in AGENT_SPECS:
        bus.register(name, protocol_handler(name))
    routes = {name: spec.handoff_targets for name, spec in AGENT_SPECS.items()}
    handoffs = HandoffManager(routes, bus)

    def handoff_handler(config: dict, inputs: dict) -> dict:
        request = HandoffRequest(
            source=str(config["source"]),
            destination=str(config["destination"]),
            task=str(config.get("task", "")),
            context_summary=json.dumps(inputs, ensure_ascii=False)[:8_000],
            reason=str(config.get("reason", "")),
            trace_id=trace_id,
            return_policy=str(config.get("return_policy", "return_to_coordinator")),
        )
        reply = handoffs.transfer(request, depth=int(config.get("depth", 1)))
        return reply.payload

    def chain_handler(config: dict, inputs: dict) -> dict:
        problem = str(config.get("problem", json.dumps(inputs, ensure_ascii=False)))

        def build_step(problem_text: str, steps: list, index: int) -> dict:
            return _json_model(
                client,
                model,
                (
                    "Return a JSON decision record with summary, decision, evidence "
                    "(an array), confidence (0..1), final (boolean), and optional answer. "
                    "Give concise inspectable reasoning summaries, never private chain-of-thought."
                ),
                {"problem": problem_text, "previous_steps": steps, "step_number": index + 1},
            )

        return reasoning_chain(problem, build_step, int(config.get("max_steps", 4)))

    def tree_handler(config: dict, inputs: dict) -> dict:
        problem = str(config.get("problem", json.dumps(inputs, ensure_ascii=False)))

        def generate(problem_text: str, parent: dict, count: int) -> list[str]:
            value = _json_model(
                client,
                model,
                f"Return JSON with an approaches array containing {count} distinct concise proposals.",
                {"problem": problem_text, "parent": parent},
            )
            return value.get("approaches", [])[:count]

        def evaluate(problem_text: str, thought: dict) -> dict:
            return _json_model(
                client,
                model,
                "Return JSON with score (0..1) and a concise critique.",
                {"problem": problem_text, "candidate": thought},
            )

        return tree_of_thought(
            problem,
            generate,
            evaluate,
            int(config.get("branches", 3)),
            int(config.get("levels", 2)),
        )

    runtime = WorkflowRuntime(
        agent_handlers=agent_handlers,
        tool_handlers=TOOL_FUNCTIONS,
        reasoning_handler=chain_handler,
        tree_handler=tree_handler,
        handoff_handler=handoff_handler,
    )
    return runtime, collected_events, bus, handoffs


def run_workflow(
    api_key: str,
    model: str,
    definition: dict,
    approval_provider: Callable | None = None,
) -> dict:
    """Validate and execute a workflow, pausing or asking for HITL decisions."""
    workflow = Workflow.from_dict(definition)
    trace_id = str(uuid4())
    runtime, specialist_events, bus, handoffs = build_workflow_runtime(
        api_key, model, trace_id
    )
    run = runtime.start(workflow)
    while run.workflow.status == "waiting_for_human" and approval_provider:
        pending = [
            approval
            for approval in run.approvals.values()
            if approval.status.value == "requested"
        ]
        if not pending:
            break
        for approval in pending:
            decision = approval_provider(approval.to_dict())
            run = runtime.resolve_approval(
                run,
                approval.approval_id,
                decision.get("decision", "rejected"),
                decision.get("note", ""),
                decision.get("edited_input"),
            )
    result = run.to_dict()
    result["trace_id"] = trace_id
    result["specialist_events"] = specialist_events
    result["protocol_events"] = bus.events
    result["handoff_events"] = handoffs.events
    save_workflow_run(result)
    return result
