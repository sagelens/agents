"""Reusable stateless execution loop for narrowly scoped specialist agents."""

# Import JSON for bounded evidence and synthesis prompts.
import json
# Import UTC-aware timestamps for trace correlation.
from datetime import datetime, timezone
# Import timing helpers for retries and latency.
from time import perf_counter, sleep
# Import UUID generation for agent runs and steps.
from uuid import uuid4

# Import the Gemini SDK.
from google import genai
# Import typed API errors and message objects.
from google.genai import errors, types

# Import agent configuration.
from .agents import AgentSpec
# Import the low-level tool registry and declarations.
from .tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS

# Retry one provider-side error after SDK retries finish.
MODEL_API_ATTEMPTS = 2


# Return a stable UTC timestamp.
def now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    # Use UTC across coordinator and parallel specialists.
    return datetime.now(timezone.utc).isoformat()


# Normalize optional provider token counters.
def token_usage(response) -> dict:
    """Return stable input, output, thinking, and total token fields."""
    # Read the provider metadata once.
    usage = response.usage_metadata
    # Normalize absent counters to zero.
    return {
        "input": getattr(usage, "prompt_token_count", 0) or 0,
        "output": getattr(usage, "candidates_token_count", 0) or 0,
        "thinking": getattr(usage, "thoughts_token_count", 0) or 0,
        "total": getattr(usage, "total_token_count", 0) or 0,
    }


# Select only declarations allowed by one specialist.
def declarations_for(spec: AgentSpec) -> list[dict]:
    """Return tool schemas in the specialist's explicit allowlist."""
    # Filter by stable declaration name.
    return [
        declaration
        for declaration in TOOL_DECLARATIONS
        if declaration["name"] in spec.tool_names
    ]


# Bound serialized observations before returning them to another model.
def bounded_evidence(evidence: list[dict], limit: int) -> list[dict]:
    """Keep evidence JSON beneath a configured character limit."""
    # Serialize compactly to measure the actual coordinator payload.
    encoded = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    # Preserve complete structured evidence when it fits.
    if len(encoded) <= limit:
        # Return the original objects.
        return evidence
    # Return an explicit preview instead of invalid truncated JSON.
    return [{"truncated": True, "preview": encoded[:limit]}]


# Gather artifacts from successful or failed tool observations.
def collect_artifacts(evidence: list[dict]) -> list[dict]:
    """Return validated artifact metadata without duplicates."""
    # Index artifacts by trusted host path.
    by_path = {}
    # Visit every low-level tool observation.
    for item in evidence:
        # Read a dictionary result defensively.
        result = item.get("result", {})
        # Skip non-dictionary results.
        if not isinstance(result, dict):
            # Continue scanning.
            continue
        # Visit artifacts returned by tools such as run_python.
        for artifact in result.get("artifacts", []):
            # Read the validated path.
            path = artifact.get("path")
            # Keep only artifacts with a path.
            if path:
                # The newest identical metadata wins.
                by_path[path] = artifact
    # Preserve insertion order while returning plain values.
    return list(by_path.values())


# Run one specialist with a fresh client and no retained private memory.
def run_specialist(
    api_key: str,
    model: str,
    spec: AgentSpec,
    task: str,
    context: str,
    trace_id: str,
    parent_step_id: str,
) -> dict:
    """Execute a specialist and return its result plus hierarchical events."""
    # Give this invocation an identity independent from the user turn.
    agent_run_id = str(uuid4())
    # Create a separate client so parallel specialists do not share mutable SDK state.
    client = genai.Client(api_key=api_key)
    # Start end-to-end specialist timing.
    started = perf_counter()
    # Collect specialist-local events for later attachment to the parent trace.
    events = []
    # Collect low-level observations for synthesis and structured evidence.
    evidence = []
    # Move directly to synthesis after a non-correctable environment failure.
    force_synthesis = False
    # Format the focused, stateless specialist input.
    initial_text = f"Task:\n{task}\n\nRelevant context:\n{context or 'None'}"
    # Start a new specialist conversation with no coordinator history.
    contents = [types.Content(role="user", parts=[types.Part(text=initial_text)])]
    # Build the specialist tool configuration.
    tool_config = types.GenerateContentConfig(
        system_instruction=spec.instructions,
        tools=[types.Tool(function_declarations=declarations_for(spec))],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    # Build a tool-free final synthesis configuration.
    final_config = types.GenerateContentConfig(
        system_instruction=(
            spec.instructions
            + "\nTool gathering is complete. Return the concise specialist answer now."
        )
    )
    # Reserve one final model call after bounded tool rounds.
    max_model_calls = spec.max_tool_rounds + 1
    # Visit each specialist model round.
    for call_number in range(1, max_model_calls + 1):
        # Decide whether this is evidence gathering or forced synthesis.
        gathering = call_number <= spec.max_tool_rounds and not force_synthesis
        # Use tool schemas only while gathering.
        current_config = tool_config if gathering else final_config
        # Preserve native function history only while tools remain enabled.
        if gathering:
            # Send the exact specialist conversation.
            request_contents = contents
        else:
            # Isolate final synthesis from function-call momentum.
            final_text = (
                f"Task:\n{task}\n\nEvidence:\n"
                f"{json.dumps(bounded_evidence(evidence, spec.output_size_limit), ensure_ascii=False)}"
            )
            # Send ordinary text without tools.
            request_contents = [
                types.Content(role="user", parts=[types.Part(text=final_text)])
            ]
        # Begin model-call timing.
        model_started = perf_counter()
        # Start without a response for retry control.
        response = None
        # Retry one provider-side server failure.
        for attempt in range(1, MODEL_API_ATTEMPTS + 1):
            # Convert provider errors into specialist events.
            try:
                # Call Gemma with this specialist's prompt and tools.
                response = client.models.generate_content(
                    model=model,
                    contents=request_contents,
                    config=current_config,
                )
                # Leave the retry loop on success.
                break
            # Catch Gemini API errors.
            except errors.APIError as error:
                # Record the failed granular interaction.
                events.append(
                    {
                        "step_id": str(uuid4()),
                        "trace_id": trace_id,
                        "parent_step_id": parent_step_id,
                        "agent_name": spec.name,
                        "agent_run_id": agent_run_id,
                        "depth": 1,
                        "type": "model_error",
                        "number": call_number,
                        "attempt": attempt,
                        "status_code": error.code,
                        "error": str(error),
                        "latency_ms": round((perf_counter() - model_started) * 1000, 2),
                        "timestamp": now(),
                    }
                )
                # Retry only server failures with an attempt remaining.
                if error.code >= 500 and attempt < MODEL_API_ATTEMPTS:
                    # Pause briefly before retrying.
                    sleep(1)
                    # Retry the same call.
                    continue
                # Return a typed specialist failure.
                return _specialist_result(
                    spec,
                    agent_run_id,
                    "provider_error",
                    "",
                    evidence,
                    events,
                    started,
                    str(error),
                )
        # Guard against an impossible absent response.
        if response is None:
            # Return an internal failure instead of raising into the coordinator.
            return _specialist_result(
                spec,
                agent_run_id,
                "internal_error",
                "",
                evidence,
                events,
                started,
                "Model retry loop ended without a response.",
            )
        # Read requested functions.
        function_calls = response.function_calls or []
        # Record the successful model operation.
        events.append(
            {
                "step_id": str(uuid4()),
                "trace_id": trace_id,
                "parent_step_id": parent_step_id,
                "agent_name": spec.name,
                "agent_run_id": agent_run_id,
                "depth": 1,
                "type": "model_call",
                "number": call_number,
                "decision": "call_tool" if function_calls else "final_answer",
                "tool_names": [call.name for call in function_calls],
                "latency_ms": round((perf_counter() - model_started) * 1000, 2),
                "tokens": token_usage(response),
                "timestamp": now(),
            }
        )
        # Return when the specialist produces prose.
        if not function_calls:
            # Normalize an empty response.
            answer = response.text or "The specialist returned no text."
            # Mark tool failures as partial only when evidence contains a failed result.
            partial = any(
                isinstance(item.get("result"), dict)
                and item["result"].get("ok") is False
                for item in evidence
            )
            # Return the complete specialist result.
            return _specialist_result(
                spec,
                agent_run_id,
                "partial" if partial else "completed",
                answer,
                evidence,
                events,
                started,
                None,
            )
        # Reject function calls during the tool-free final round.
        if not gathering:
            # Stop malformed tool-call continuation.
            break
        # Preserve the exact model function-call content.
        contents.append(response.candidates[0].content)
        # Collect corresponding function-response parts.
        response_parts = []
        # Execute every requested low-level tool sequentially.
        for call in function_calls:
            # Start tool timing.
            tool_started = perf_counter()
            # Validate the tool against both global and specialist allowlists.
            tool = (
                TOOL_FUNCTIONS.get(call.name)
                if call.name in spec.tool_names
                else None
            )
            # Normalize model arguments.
            arguments = dict(call.args or {})
            # Reject an unauthorized tool.
            if tool is None:
                # Return a safe observation.
                result = {"ok": False, "error": f"Tool not allowed for {spec.name}: {call.name}"}
            else:
                # Convert tool exceptions into observations.
                try:
                    # Execute only the validated function.
                    result = tool(**arguments)
                # Keep one tool failure from crashing the specialist.
                except Exception as error:
                    # Return a structured failure.
                    result = {"ok": False, "error": str(error)}
            # Store structured evidence.
            evidence.append({"tool": call.name, "arguments": arguments, "result": result})
            # Detect failures that changed Python code or another tool call cannot repair.
            if (
                isinstance(result, dict)
                and result.get("status")
                in {
                    "docker_unavailable",
                    "image_missing",
                    "timeout",
                    "output_limit",
                    "artifact_limit",
                }
            ):
                # Force the next model call to explain the existing observation.
                force_synthesis = True
            # Record the low-level tool event.
            events.append(
                {
                    "step_id": str(uuid4()),
                    "trace_id": trace_id,
                    "parent_step_id": parent_step_id,
                    "agent_name": spec.name,
                    "agent_run_id": agent_run_id,
                    "depth": 1,
                    "type": "tool_call",
                    "tool": call.name,
                    "arguments": arguments,
                    "result": result,
                    "latency_ms": round((perf_counter() - tool_started) * 1000, 2),
                    "timestamp": now(),
                }
            )
            # Tie the observation to Gemini's original function-call ID.
            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name,
                        id=call.id,
                        response={"result": result},
                    )
                )
            )
        # Return all observations for the next specialist decision.
        contents.append(types.Content(role="user", parts=response_parts))
    # Return a bounded failure when synthesis remains malformed.
    return _specialist_result(
        spec,
        agent_run_id,
        "step_limit_reached",
        "The specialist gathered evidence but did not produce a final answer.",
        evidence,
        events,
        started,
        "Specialist model-call limit reached.",
    )


# Construct one stable result shape and aggregate usage.
def _specialist_result(
    spec: AgentSpec,
    agent_run_id: str,
    status: str,
    answer: str,
    evidence: list[dict],
    events: list[dict],
    started: float,
    error: str | None,
) -> dict:
    """Build the public specialist result and retain events separately."""
    # Initialize aggregate token counters.
    tokens = {"input": 0, "output": 0, "thinking": 0, "total": 0}
    # Sum only successful model-call usage.
    for event in events:
        # Read optional token metadata.
        usage = event.get("tokens", {})
        # Add each normalized counter.
        for key in tokens:
            # Default missing fields to zero.
            tokens[key] += usage.get(key, 0)
    # Bound evidence before coordinator ingestion.
    safe_evidence = bounded_evidence(evidence, spec.output_size_limit)
    # Return result and internal events as separate keys.
    return {
        "agent": spec.name,
        "agent_run_id": agent_run_id,
        "status": status,
        "answer": answer[:8_000],
        "evidence": safe_evidence,
        "artifacts": collect_artifacts(evidence),
        "tokens": tokens,
        "latency_ms": round((perf_counter() - started) * 1000, 2),
        "error": error,
        "_events": events,
    }
