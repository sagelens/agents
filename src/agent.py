"""A small manual ReAct loop with observable model and tool steps."""

# Import JSON to place structured tool evidence into the final synthesis prompt.
import json
# Import UTC-aware timestamps for trace records.
from datetime import datetime, timezone
# Import timing helpers for latency measurements and one provider retry.
from time import perf_counter, sleep
# Import UUID generation for session, turn, trace, and step identifiers.
from uuid import uuid4

# Import the official Google Gen AI client.
from google import genai
# Import provider errors and typed request objects used by the Gemini API.
from google.genai import errors, types

# Import the system policy.
from .prompt import SYSTEM_PROMPT
# Import persistent storage helpers.
from .store import append_trajectory, load_session, save_session
# Import the executable allowlist and model-visible schemas.
from .tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS

# Allow two rounds of evidence gathering before mandatory synthesis.
MAX_TOOL_ROUNDS = 2
# Reserve one final model call that cannot request another tool.
MAX_MODEL_CALLS = MAX_TOOL_ROUNDS + 1
# Retry one provider-side failure after the SDK's own internal retries finish.
MODEL_API_ATTEMPTS = 2


# Return an ISO timestamp that is unambiguous across time zones.
def now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    # Use UTC so traces from different machines remain comparable.
    return datetime.now(timezone.utc).isoformat()


# Convert our simple stored messages into Gemini content objects.
def history_to_contents(messages: list[dict]) -> list[types.Content]:
    """Translate public session messages into Gemini's user/model history."""
    # Start with an empty API history.
    contents = []
    # Visit messages in their original chronological order.
    for message in messages:
        # Gemini calls assistant messages "model" messages.
        role = "model" if message["role"] == "assistant" else "user"
        # Tool details are stored for humans but excluded from later chat context.
        if message["role"] == "tool":
            # Skip because each completed turn already has an assistant summary.
            continue
        # Add one text part for this public message.
        contents.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))
    # Return API-ready conversation history.
    return contents


# Read token counters without failing when a provider omits one.
def token_usage(response) -> dict:
    """Normalize Gemini usage metadata into stable names."""
    # Read the optional metadata object.
    usage = response.usage_metadata
    # Return zeros when the provider does not report a field.
    return {
        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
        "thinking_tokens": getattr(usage, "thoughts_token_count", 0) or 0,
        "total_tokens": getattr(usage, "total_token_count", 0) or 0,
    }


# Execute one complete user interaction.
def run_turn(client: genai.Client, model: str, session_id: str, user_text: str) -> dict:
    """Run the bounded model-tool loop and return the answer plus its trajectory."""
    # Generate IDs at their correct scopes.
    turn_id = str(uuid4())
    # A trace groups every operation required for this turn.
    trace_id = str(uuid4())
    # Load previous public turns to provide multi-turn context.
    saved_messages = load_session(session_id)
    # Convert stored messages and add the new user message.
    contents = history_to_contents(saved_messages)
    # Represent the new input in Gemini's content format.
    user_content = types.Content(role="user", parts=[types.Part(text=user_text)])
    # Add it to the API conversation.
    contents.append(user_content)
    # Start the append-only list of observable execution events.
    events = []
    # Keep bounded tool observations for a fresh, tool-free synthesis request.
    gathered_evidence = []
    # Start measuring end-to-end turn latency.
    turn_started = perf_counter()
    # Prepare function schemas while disabling hidden automatic execution.
    config = types.GenerateContentConfig(
        # Apply durable behavior separately from conversation data.
        system_instruction=SYSTEM_PROMPT,
        # Give the model the two function declarations.
        tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
        # Keep orchestration in our code so every call is observable.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    # Prepare a final configuration containing no function declarations.
    final_config = types.GenerateContentConfig(
        # Preserve the durable policy while explicitly ending evidence gathering.
        system_instruction=(
            SYSTEM_PROMPT
            + "\nTool gathering is complete. Answer now using the available evidence. "
            + "Do not request another tool."
        ),
    )
    # Make at most the configured number of model calls.
    for model_call_number in range(1, MAX_MODEL_CALLS + 1):
        # Remove tools on the last call so the loop must terminate with synthesis.
        current_config = config if model_call_number <= MAX_TOOL_ROUNDS else final_config
        # Use normal function-call history only while gathering evidence.
        if model_call_number <= MAX_TOOL_ROUNDS:
            # Preserve Gemini's exact model and function-response sequence.
            request_contents = contents
        else:
            # Create a fresh text-only request so prior tool-call syntax cannot continue the loop.
            synthesis_prompt = (
                f"User question:\n{user_text}\n\n"
                f"Retrieved evidence:\n{json.dumps(gathered_evidence, ensure_ascii=False)}\n\n"
                "Answer the user directly. Cite repository-relative file paths and line numbers "
                "when the evidence contains them. For a question asking where something is stored, "
                "identify the most relevant matching file instead of merely summarizing a line."
            )
            # Send only ordinary user text with no tool declarations.
            request_contents = [
                types.Content(role="user", parts=[types.Part(text=synthesis_prompt)])
            ]
        # Record when this individual model request begins.
        model_started = perf_counter()
        # Start without a response so the retry loop can populate it.
        response = None
        # Make one extra attempt only after a provider API failure.
        for api_attempt in range(1, MODEL_API_ATTEMPTS + 1):
            # Convert provider failures into trace events instead of crashing the CLI.
            try:
                # Ask Gemma either to answer or, when enabled, select a declared tool.
                response = client.models.generate_content(
                    model=model,
                    contents=request_contents,
                    config=current_config,
                )
                # Leave the retry loop after a successful provider response.
                break
            # Catch documented Gemini API errors such as HTTP 500.
            except errors.APIError as error:
                # Record the failed provider interaction at its true granularity.
                events.append(
                    {
                        "step_id": str(uuid4()),
                        "type": "model_error",
                        "number": model_call_number,
                        "attempt": api_attempt,
                        "status_code": error.code,
                        "error": str(error),
                        "latency_ms": round((perf_counter() - model_started) * 1000, 2),
                        "timestamp": now(),
                    }
                )
                # Retry only server-side failures and only when an attempt remains.
                if error.code >= 500 and api_attempt < MODEL_API_ATTEMPTS:
                    # Wait briefly to avoid immediately hitting the same transient condition.
                    sleep(1)
                    # Try the same model interaction again.
                    continue
                # Build a concise response that keeps the terminal session alive.
                answer = "Gemini could not complete this turn. Please try the question again."
                # Record the failed trajectory without adding it to conversation memory.
                trajectory = {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "trace_id": trace_id,
                    "started_at": events[0]["timestamp"],
                    "finished_at": now(),
                    "latency_ms": round((perf_counter() - turn_started) * 1000, 2),
                    "events": events,
                    "total_tokens": 0,
                    "status": "provider_error",
                }
                # Persist the failure for debugging and reliability analysis.
                append_trajectory(trajectory)
                # Return normally instead of exposing a Python traceback.
                return {"answer": answer, "trajectory": trajectory}
        # Guard against an impossible missing response after the retry loop.
        if response is None:
            # Raise a local programming error rather than using an undefined response.
            raise RuntimeError("The model retry loop ended without a response.")
        # Measure only the model API request.
        model_latency_ms = round((perf_counter() - model_started) * 1000, 2)
        # Read the function calls, defaulting to an empty list.
        function_calls = response.function_calls or []
        # Add the model operation to the trace without private reasoning text.
        events.append(
            {
                "step_id": str(uuid4()),
                "type": "model_call",
                "number": model_call_number,
                "decision": "call_tool" if function_calls else "final_answer",
                "tool_names": [call.name for call in function_calls],
                "latency_ms": model_latency_ms,
                "tokens": token_usage(response),
                "timestamp": now(),
            }
        )
        # Finish when the model produced ordinary text instead of a tool request.
        if not function_calls:
            # Read the final public answer.
            answer = response.text or "The model returned no text."
            # Add the user message to durable public history.
            saved_messages.append({"role": "user", "content": user_text, "turn_id": turn_id})
            # Add the assistant answer to durable public history.
            saved_messages.append({"role": "assistant", "content": answer, "turn_id": turn_id})
            # Save multi-turn context for a later process invocation.
            save_session(session_id, saved_messages)
            # Construct the complete trajectory record.
            trajectory = {
                "session_id": session_id,
                "turn_id": turn_id,
                "trace_id": trace_id,
                "started_at": events[0]["timestamp"],
                "finished_at": now(),
                "latency_ms": round((perf_counter() - turn_started) * 1000, 2),
                "events": events,
                "total_tokens": sum(event.get("tokens", {}).get("total_tokens", 0) for event in events),
                "status": "completed",
            }
            # Persist the trace independently from the chat history.
            append_trajectory(trajectory)
            # Return both useful output and inspection metadata.
            return {"answer": answer, "trajectory": trajectory}
        # Preserve the exact model content, including its function-call IDs.
        contents.append(response.candidates[0].content)
        # Collect all tool responses into one user-role content message.
        function_response_parts = []
        # Execute every requested tool call in order.
        for call in function_calls:
            # Begin measuring this individual tool.
            tool_started = perf_counter()
            # Reject any function name outside the explicit allowlist.
            tool = TOOL_FUNCTIONS.get(call.name)
            # Prepare a safe result for an unknown function.
            if tool is None:
                # Do not use eval or dynamically import model-provided names.
                result = {"ok": False, "error": f"Unknown tool: {call.name}"}
            else:
                # Convert provider arguments into an ordinary dictionary.
                arguments = dict(call.args or {})
                # Catch tool failures so the model can explain or recover.
                try:
                    # Execute only the allowlisted function.
                    result = tool(**arguments)
                # Convert expected and unexpected tool failures into observations.
                except Exception as error:
                    # Avoid crashing the entire session on one network failure.
                    result = {"ok": False, "error": str(error)}
            # Measure the tool independently from model latency.
            tool_latency_ms = round((perf_counter() - tool_started) * 1000, 2)
            # Add a transparent tool event without secrets.
            events.append(
                {
                    "step_id": str(uuid4()),
                    "type": "tool_call",
                    "tool": call.name,
                    "arguments": dict(call.args or {}),
                    "result": result,
                    "latency_ms": tool_latency_ms,
                    "timestamp": now(),
                }
            )
            # Preserve the bounded observation for the final isolated synthesis call.
            gathered_evidence.append(
                {
                    "tool": call.name,
                    "arguments": dict(call.args or {}),
                    "result": result,
                }
            )
            # Construct the function response tied to the model's call ID.
            function_response_parts.append(
                # Build the nested object directly because the SDK helper does not accept an ID.
                types.Part(
                    # Keep the ID so Gemini can match this result to its original request.
                    function_response=types.FunctionResponse(
                        # Copy the model-generated function name.
                        name=call.name,
                        # Copy the model-generated call ID.
                        id=call.id,
                        # Wrap the tool result in a stable response property.
                        response={"result": result},
                    )
                )
            )
        # Return all tool observations to the model for its next decision.
        contents.append(types.Content(role="user", parts=function_response_parts))
    # Produce a controlled response only if the mandatory synthesis call was malformed.
    answer = "I gathered evidence but could not produce a final answer."
    # Save the user's input and controlled assistant response.
    saved_messages.extend(
        [
            {"role": "user", "content": user_text, "turn_id": turn_id},
            {"role": "assistant", "content": answer, "turn_id": turn_id},
        ]
    )
    # Persist the bounded-loop result.
    save_session(session_id, saved_messages)
    # Record the stopped trajectory.
    trajectory = {
        "session_id": session_id,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "started_at": events[0]["timestamp"],
        "finished_at": now(),
        "latency_ms": round((perf_counter() - turn_started) * 1000, 2),
        "events": events,
        "total_tokens": sum(event.get("tokens", {}).get("total_tokens", 0) for event in events),
        "status": "step_limit_reached",
    }
    # Append the stopped trace for debugging.
    append_trajectory(trajectory)
    # Return the controlled answer and evidence.
    return {"answer": answer, "trajectory": trajectory}
