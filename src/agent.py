"""User-facing coordinator for the framework-free multi-agent system."""

# Import JSON for isolated final synthesis prompts.
import json
# Import bounded parallel execution for independent specialists.
from concurrent.futures import ThreadPoolExecutor, as_completed
# Import UTC-aware timestamps for trajectories.
from datetime import datetime, timezone
# Import timing helpers for latency and provider retries.
from time import perf_counter, sleep
# Import UUID generation for trace events.
from uuid import uuid4

# Import the Gemini SDK.
from google import genai
# Import provider errors and typed content objects.
from google.genai import errors, types

# Import the specialist registry and delegation schemas.
from .agents import AGENT_SPECS, DELEGATION_DECLARATIONS, DELEGATION_TO_AGENT
# Import the coordinator's routing instructions.
from .prompt import COORDINATOR_PROMPT
# Import the reusable specialist runtime and shared token normalizer.
from .runtime import now, run_specialist, token_usage
# Import conversation and trajectory persistence.
from .store import append_trajectory, load_session, save_session
# Import optional Phoenix export.
from .telemetry import export_trajectory

# Permit at most two rounds of coordinator delegation.
MAX_DELEGATION_ROUNDS = 2
# Reserve one final tool-free synthesis call.
MAX_COORDINATOR_CALLS = MAX_DELEGATION_ROUNDS + 1
# Limit total specialist invocations in one user turn.
MAX_SPECIALIST_INVOCATIONS = 3
# Bound focused task and context fields independently.
MAX_DELEGATION_TEXT_BYTES = 2 * 1024
# Retry one provider-side failure after SDK retries.
MODEL_API_ATTEMPTS = 2


# Convert stored public messages into Gemini contents.
def history_to_contents(messages: list[dict]) -> list[types.Content]:
    """Translate public user and assistant messages into Gemini roles."""
    # Start with an empty provider history.
    contents = []
    # Preserve chronological order.
    for message in messages:
        # Ignore any legacy persisted tool records.
        if message["role"] == "tool":
            # Specialists are intentionally stateless.
            continue
        # Translate assistant into Gemini's model role.
        role = "model" if message["role"] == "assistant" else "user"
        # Append one public text message.
        contents.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))
    # Return provider-ready history.
    return contents


# Enforce the focused delegation payload boundary without breaking UTF-8.
def bounded_text(value: str) -> str:
    """Return no more than two kilobytes of UTF-8 text."""
    # Encode with a deterministic representation.
    encoded = value.encode("utf-8")
    # Preserve text that already fits.
    if len(encoded) <= MAX_DELEGATION_TEXT_BYTES:
        # Return it unchanged.
        return value
    # Decode a byte preview while dropping an incomplete final code point.
    return encoded[:MAX_DELEGATION_TEXT_BYTES].decode("utf-8", errors="ignore")


# Remove internal runtime fields before returning results to the coordinator model.
def public_specialist_result(result: dict) -> dict:
    """Strip internal event transport from a specialist result."""
    # Copy only public fields.
    return {key: value for key, value in result.items() if not key.startswith("_")}


# Aggregate trusted artifact metadata from specialist results.
def collect_turn_artifacts(results: list[dict]) -> list[dict]:
    """Return unique artifacts keyed by host path."""
    # Index by validated host path.
    by_path = {}
    # Visit each specialist result.
    for result in results:
        # Visit its artifacts.
        for artifact in result.get("artifacts", []):
            # Read the trusted path.
            path = artifact.get("path")
            # Preserve only path-bearing artifacts.
            if path:
                # Keep one copy.
                by_path[path] = artifact
    # Return stable insertion order.
    return list(by_path.values())


# Call the coordinator model with one explicit retry policy.
def call_coordinator_model(
    client: genai.Client,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
    call_number: int,
    trace_id: str,
    events: list[dict],
) -> object | None:
    """Return a model response or record a terminal provider error."""
    # Start granular provider timing.
    started = perf_counter()
    # Retry one server-side error.
    for attempt in range(1, MODEL_API_ATTEMPTS + 1):
        # Convert Gemini errors into trajectory events.
        try:
            # Execute the coordinator request.
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        # Catch documented API failures.
        except errors.APIError as error:
            # Record the failed request.
            events.append(
                {
                    "step_id": str(uuid4()),
                    "trace_id": trace_id,
                    "parent_step_id": None,
                    "agent_name": "coordinator",
                    "agent_run_id": trace_id,
                    "depth": 0,
                    "type": "model_error",
                    "number": call_number,
                    "attempt": attempt,
                    "status_code": error.code,
                    "error": str(error),
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "timestamp": now(),
                }
            )
            # Retry only server failures with an attempt remaining.
            if error.code >= 500 and attempt < MODEL_API_ATTEMPTS:
                # Pause briefly before retrying.
                sleep(1)
                # Repeat the request.
                continue
            # Signal terminal provider failure.
            return None
    # Guard against unexpected retry-loop fallthrough.
    return None


# Execute one complete multi-agent user turn.
def run_turn(api_key: str, model: str, session_id: str, user_text: str) -> dict:
    """Route, delegate, synthesize, persist, and return one user-facing answer."""
    # Generate turn and trace identities.
    turn_id = str(uuid4())
    # Use one trace across coordinator and specialists.
    trace_id = str(uuid4())
    # Create the coordinator's private client.
    client = genai.Client(api_key=api_key)
    # Load only public conversation memory.
    saved_messages = load_session(session_id)
    # Convert public history.
    contents = history_to_contents(saved_messages)
    # Add this user message.
    contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    # Start hierarchical trajectory collection.
    events = []
    # Collect public specialist outputs for final synthesis.
    specialist_results = []
    # Track the global delegation budget.
    specialist_invocations = 0
    # Prevent redundant use of the same specialist across coordinator rounds.
    used_agents_this_turn = set()
    # Start end-to-end turn timing.
    turn_started = perf_counter()
    # Give the coordinator only agent-as-tool declarations.
    delegation_config = types.GenerateContentConfig(
        system_instruction=COORDINATOR_PROMPT,
        tools=[types.Tool(function_declarations=DELEGATION_DECLARATIONS)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    # Build a final configuration with no delegation tools.
    final_config = types.GenerateContentConfig(
        system_instruction=(
            COORDINATOR_PROMPT
            + "\nDelegation is complete. Synthesize the final user-facing answer now."
        )
    )
    # Run bounded coordinator rounds.
    for call_number in range(1, MAX_COORDINATOR_CALLS + 1):
        # Keep delegation enabled for two rounds.
        delegating = call_number <= MAX_DELEGATION_ROUNDS
        # Select the matching configuration.
        current_config = delegation_config if delegating else final_config
        # Preserve native function history only during delegation.
        if delegating:
            # Send public history plus coordinator delegation history.
            request_contents = contents
        else:
            # Isolate synthesis from function-call momentum.
            synthesis_text = (
                f"User request:\n{user_text}\n\n"
                f"Specialist results:\n{json.dumps(specialist_results, ensure_ascii=False)}\n\n"
                "Answer directly, preserve exact evidence and host artifact paths, "
                "and disclose any specialist failure."
            )
            # Send a fresh text-only synthesis request.
            request_contents = [
                types.Content(role="user", parts=[types.Part(text=synthesis_text)])
            ]
        # Start coordinator-call timing.
        model_started = perf_counter()
        # Execute the coordinator model call.
        response = call_coordinator_model(
            client,
            model,
            request_contents,
            current_config,
            call_number,
            trace_id,
            events,
        )
        # Return gracefully when provider retries are exhausted.
        if response is None:
            # Build a stable user-facing failure.
            answer = "Gemini could not complete this turn. Please try the question again."
            # Finish without adding a failed turn to public memory.
            return finish_turn(
                session_id,
                turn_id,
                trace_id,
                answer,
                user_text,
                saved_messages,
                events,
                specialist_results,
                turn_started,
                "provider_error",
                save_messages=False,
            )
        # Read requested agent delegations.
        function_calls = response.function_calls or []
        # Give this coordinator operation a stable step ID.
        coordinator_step_id = str(uuid4())
        # Record the successful coordinator operation.
        events.append(
            {
                "step_id": coordinator_step_id,
                "trace_id": trace_id,
                "parent_step_id": None,
                "agent_name": "coordinator",
                "agent_run_id": trace_id,
                "depth": 0,
                "type": "model_call",
                "number": call_number,
                "decision": "delegate" if function_calls else "final_answer",
                "tool_names": [call.name for call in function_calls],
                "latency_ms": round((perf_counter() - model_started) * 1000, 2),
                "tokens": token_usage(response),
                "timestamp": now(),
            }
        )
        # Finish when the coordinator produces prose.
        if not function_calls:
            # Normalize empty text.
            answer = response.text or "The coordinator returned no text."
            # Persist and return the completed turn.
            return finish_turn(
                session_id,
                turn_id,
                trace_id,
                answer,
                user_text,
                saved_messages,
                events,
                specialist_results,
                turn_started,
                "completed",
            )
        # Do not execute malformed delegation calls during final synthesis.
        if not delegating:
            # Leave the loop for controlled termination.
            break
        # Preserve the exact coordinator function-call content.
        contents.append(response.candidates[0].content)
        # Track one invocation per specialist in this round.
        seen_agents = set()
        # Keep work records aligned with original function calls.
        work_items = []
        # Validate every requested delegation.
        for index, call in enumerate(function_calls):
            # Resolve the specialist identity from the explicit registry.
            agent_name = DELEGATION_TO_AGENT.get(call.name)
            # Normalize model arguments.
            arguments = dict(call.args or {})
            # Prepare a rejection reason when needed.
            rejection = None
            # Reject unknown delegation names.
            if agent_name is None:
                # Set the typed reason.
                rejection = f"Unknown delegation tool: {call.name}"
            # Reject duplicate use of one specialist in the same round.
            elif agent_name in seen_agents:
                # Set the typed reason.
                rejection = f"{agent_name} may run only once per delegation round."
            # Reject repeated use of a specialist elsewhere in this turn.
            elif agent_name in used_agents_this_turn:
                # The specialist already had its own two internal tool rounds.
                rejection = f"{agent_name} may run only once per user turn."
            # Enforce the global invocation budget.
            elif specialist_invocations >= MAX_SPECIALIST_INVOCATIONS:
                # Set the typed reason.
                rejection = "The turn reached its specialist invocation limit."
            # Record a rejected call without starting a specialist.
            if rejection:
                # Store a public rejected result.
                result = {
                    "agent": agent_name or "unknown",
                    "agent_run_id": None,
                    "status": "rejected",
                    "answer": "",
                    "evidence": [],
                    "artifacts": [],
                    "tokens": {"input": 0, "output": 0, "thinking": 0, "total": 0},
                    "latency_ms": 0,
                    "error": rejection,
                }
                # Keep it in coordinator evidence.
                specialist_results.append(result)
                # Preserve its original position.
                work_items.append(
                    {
                        "index": index,
                        "call": call,
                        "result": result,
                        "future": None,
                        "delegation_step_id": None,
                    }
                )
                # Continue validating other calls.
                continue
            # Mark this specialist used in the current round.
            seen_agents.add(agent_name)
            # Mark this specialist used for the complete turn.
            used_agents_this_turn.add(agent_name)
            # Consume one global invocation slot.
            specialist_invocations += 1
            # Resolve the immutable specialist spec.
            spec = AGENT_SPECS[agent_name]
            # Bound the focused task.
            task = bounded_text(str(arguments.get("task", "")))
            # Bound optional brief context.
            context = bounded_text(str(arguments.get("context", "")))
            # Create the parent delegation step.
            delegation_step_id = str(uuid4())
            # Record fan-out start before launching work.
            events.append(
                {
                    "step_id": delegation_step_id,
                    "trace_id": trace_id,
                    "parent_step_id": coordinator_step_id,
                    "agent_name": "coordinator",
                    "agent_run_id": trace_id,
                    "target_agent": agent_name,
                    "depth": 0,
                    "type": "delegation_start",
                    "task": task,
                    "context": context,
                    "timestamp": now(),
                }
            )
            # Store validated work for concurrent launch.
            work_items.append(
                {
                    "index": index,
                    "call": call,
                    "spec": spec,
                    "task": task,
                    "context": context,
                    "delegation_step_id": delegation_step_id,
                }
            )
        # Select only accepted work.
        accepted = [item for item in work_items if item.get("delegation_step_id")]
        # Run same-response specialists concurrently with separate clients.
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(accepted)))) as executor:
            # Map futures back to work records.
            future_to_item = {
                executor.submit(
                    run_specialist,
                    api_key,
                    model,
                    item["spec"],
                    item["task"],
                    item["context"],
                    trace_id,
                    item["delegation_step_id"],
                ): item
                for item in accepted
            }
            # Consume results as specialists finish.
            for future in as_completed(future_to_item):
                # Recover the associated call.
                item = future_to_item[future]
                # Convert unexpected worker failures into structured results.
                try:
                    # Read the specialist result.
                    internal_result = future.result()
                # Isolate a worker exception from the whole turn.
                except Exception as error:
                    # Build the common failed shape.
                    internal_result = {
                        "agent": item["spec"].name,
                        "agent_run_id": str(uuid4()),
                        "status": "internal_error",
                        "answer": "",
                        "evidence": [],
                        "artifacts": [],
                        "tokens": {"input": 0, "output": 0, "thinking": 0, "total": 0},
                        "latency_ms": 0,
                        "error": str(error),
                        "_events": [],
                    }
                # Attach specialist events beneath the delegation step.
                events.extend(internal_result.get("_events", []))
                # Strip transport-only fields before coordinator ingestion.
                result = public_specialist_result(internal_result)
                # Store the public result on its work item.
                item["result"] = result
                # Add it to final synthesis evidence.
                specialist_results.append(result)
                # Record fan-in completion.
                events.append(
                    {
                        "step_id": str(uuid4()),
                        "trace_id": trace_id,
                        "parent_step_id": item["delegation_step_id"],
                        "agent_name": "coordinator",
                        "agent_run_id": trace_id,
                        "target_agent": result["agent"],
                        "target_agent_run_id": result["agent_run_id"],
                        "depth": 0,
                        "type": "delegation_end",
                        "status": result["status"],
                        "latency_ms": result["latency_ms"],
                        "timestamp": now(),
                    }
                )
        # Build function responses in the original model-call order.
        function_response_parts = []
        # Sort by original call position.
        for item in sorted(work_items, key=lambda value: value["index"]):
            # Read the public result.
            result = item["result"]
            # Tie the agent result to the coordinator's call ID.
            function_response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=item["call"].name,
                        id=item["call"].id,
                        response={"result": result},
                    )
                )
            )
        # Return all specialist results to the coordinator.
        contents.append(types.Content(role="user", parts=function_response_parts))
    # Return a controlled answer only if final synthesis remains malformed.
    answer = "The coordinator reached its delegation limit without a final answer."
    # Persist the bounded termination.
    return finish_turn(
        session_id,
        turn_id,
        trace_id,
        answer,
        user_text,
        saved_messages,
        events,
        specialist_results,
        turn_started,
        "step_limit_reached",
    )


# Build, persist, and return one top-level turn result.
def finish_turn(
    session_id: str,
    turn_id: str,
    trace_id: str,
    answer: str,
    user_text: str,
    saved_messages: list[dict],
    events: list[dict],
    specialist_results: list[dict],
    turn_started: float,
    status: str,
    save_messages: bool = True,
) -> dict:
    """Finalize public memory, aggregate metrics, and append the trajectory."""
    # Persist only successful or controlled public turns.
    if save_messages:
        # Add the user input.
        saved_messages.append({"role": "user", "content": user_text, "turn_id": turn_id})
        # Add only the coordinator's final answer.
        saved_messages.append({"role": "assistant", "content": answer, "turn_id": turn_id})
        # Save coordinator-owned public history.
        save_session(session_id, saved_messages)
    # Initialize whole-turn token counters.
    token_totals = {"input": 0, "output": 0, "thinking": 0, "total": 0}
    # Aggregate all coordinator and specialist model calls exactly once.
    for event in events:
        # Read optional model usage.
        usage = event.get("tokens", {})
        # Sum each normalized field.
        for key in token_totals:
            # Default absent values to zero.
            token_totals[key] += usage.get(key, 0)
    # Aggregate trusted artifacts from specialist results.
    artifacts = collect_turn_artifacts(specialist_results)
    # Build a concise per-agent summary.
    agent_summaries = [
        {
            "agent": result["agent"],
            "agent_run_id": result["agent_run_id"],
            "status": result["status"],
            "tokens": result["tokens"],
            "latency_ms": result["latency_ms"],
        }
        # Preserve completion order to reflect fan-in.
        for result in specialist_results
    ]
    # Construct the hierarchical trajectory.
    trajectory = {
        "session_id": session_id,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "started_at": events[0]["timestamp"] if events else now(),
        "finished_at": now(),
        "latency_ms": round((perf_counter() - turn_started) * 1000, 2),
        "events": events,
        "agent_summaries": agent_summaries,
        "tokens": token_totals,
        "total_tokens": token_totals["total"],
        "status": status,
    }
    # Export the completed hierarchy without making Phoenix a runtime dependency.
    phoenix_trace_id = export_trajectory(
        trajectory,
        user_text,
        answer,
        specialist_results,
    )
    # Store the cross-system ID in the JSONL source of truth.
    trajectory["phoenix_trace_id"] = phoenix_trace_id
    # Append one turn-level JSON Lines record.
    append_trajectory(trajectory)
    # Return public answer, artifacts, and observability metadata.
    return {
        "answer": answer,
        "artifacts": artifacts,
        "phoenix_trace_id": phoenix_trace_id,
        "trajectory": trajectory,
    }
