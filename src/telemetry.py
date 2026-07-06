"""Optional Phoenix/OpenInference export for completed application trajectories."""

# Import JSON for bounded structured span attributes.
import json
# Import environment variables for opt-in configuration.
import os
# Import UTC timestamps and duration arithmetic.
from datetime import datetime, timedelta

# Keep provider and tracer globals so registration occurs once per process.
_provider = None
# Start without a tracer when Phoenix is disabled.
_tracer = None
# Remember the explicit project so experiment-worker context cannot override it.
_project_name = None


# Interpret familiar truthy environment values.
def _enabled() -> bool:
    """Return whether Phoenix tracing is explicitly enabled."""
    # Normalize the environment value.
    return os.getenv("PHOENIX_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


# Register a lightweight OTLP exporter without framework auto-instrumentation.
def configure_telemetry(project_name: str | None = None) -> None:
    """Configure Phoenix once; failures leave the agent fully operational."""
    # Access module globals.
    global _provider, _tracer, _project_name
    # Remain a no-op unless the user opted in.
    if not _enabled() or _tracer is not None:
        # Nothing else is required.
        return
    # Isolate all optional dependency and connection setup failures.
    try:
        # Import Phoenix only when enabled.
        from phoenix.otel import register
        # Read the full HTTP collector endpoint.
        endpoint = os.getenv(
            "PHOENIX_COLLECTOR_ENDPOINT",
            "http://localhost:6006/v1/traces",
        )
        # Resolve the requested project.
        project = project_name or os.getenv("PHOENIX_PROJECT_NAME", "agents-dev")
        # Retain the explicit project for every manually created span.
        _project_name = project
        # Register immediate export for an interactive CLI.
        _provider = register(
            endpoint=endpoint,
            protocol="http/protobuf",
            project_name=project,
            batch=False,
            auto_instrument=False,
            set_global_tracer_provider=False,
            verbose=False,
        )
        # Create this application's manual tracer.
        _tracer = _provider.get_tracer("framework-free-multi-agent")
    # Phoenix is optional, so setup must never stop the agent.
    except Exception:
        # Reset globals to their disabled state.
        _provider = None
        _tracer = None


# Flush pending spans before a short-lived CLI or eval process exits.
def shutdown_telemetry() -> None:
    """Best-effort flush that never raises into application shutdown."""
    # Skip when no provider was registered.
    if _provider is None:
        # Nothing needs flushing.
        return
    # Keep exporter failures isolated.
    try:
        # Flush synchronously for local development.
        _provider.force_flush()
    # Never hide the agent's own result.
    except Exception:
        # Ignore exporter shutdown errors.
        pass


# Convert an ISO timestamp into epoch nanoseconds accepted by OpenTelemetry.
def _epoch_ns(value: str) -> int:
    """Return an ISO timestamp as epoch nanoseconds."""
    # Parse the stored timezone-aware value.
    parsed = datetime.fromisoformat(value)
    # Convert seconds to nanoseconds.
    return int(parsed.timestamp() * 1_000_000_000)


# Infer a start timestamp from an event completion time and measured latency.
def _start_ns(event: dict) -> int:
    """Return an event's inferred start time."""
    # Parse the completion timestamp.
    ended = datetime.fromisoformat(event["timestamp"])
    # Subtract measured milliseconds.
    started = ended - timedelta(milliseconds=event.get("latency_ms", 0))
    # Return epoch nanoseconds.
    return int(started.timestamp() * 1_000_000_000)


# Serialize bounded content for an OTEL string attribute.
def _content(value: object, limit: int = 16_000) -> str:
    """Return bounded JSON or text without exposing environment data."""
    # Preserve strings and encode structured values.
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Return a bounded preview.
    return text[:limit]


# Export the custom trajectory hierarchy as an OpenInference span tree.
def export_trajectory(
    trajectory: dict,
    user_input: str,
    answer: str,
    specialist_results: list[dict],
) -> str | None:
    """Export a completed turn and return Phoenix's OpenTelemetry trace ID."""
    # Skip quickly when tracing is disabled or setup failed.
    if _tracer is None:
        # Signal that no Phoenix trace exists.
        return None
    # Keep the temporary clean-context token available for guaranteed cleanup.
    context_token = None
    # Keep the matching detach function available outside the guarded imports.
    detach_context = None
    # Keep every exporter or schema failure isolated from the agent.
    try:
        # Import OTEL context and status types lazily.
        from opentelemetry import trace
        # Import a clean root context so experiment-task suppression cannot hide app spans.
        from opentelemetry.context import Context, attach, detach
        # Remember the cleanup function.
        detach_context = detach
        # Clear suppression inherited from Phoenix's experiment task worker.
        context_token = attach(Context())
        # Import status types for successful and failed spans.
        from opentelemetry.trace import Status, StatusCode
        # Index public specialist results by run ID.
        results_by_run = {
            result.get("agent_run_id"): result
            for result in specialist_results
            if result.get("agent_run_id")
        }
        # Index events by step ID.
        events_by_step = {
            event["step_id"]: event
            for event in trajectory.get("events", [])
            if event.get("step_id")
        }
        # Pair delegation starts and ends by parent step ID.
        delegation_ends = {
            event.get("parent_step_id"): event
            for event in trajectory.get("events", [])
            if event.get("type") == "delegation_end"
        }
        # Infer the root start from measured turn latency.
        root_end = datetime.fromisoformat(trajectory["finished_at"])
        # Subtract whole-turn milliseconds.
        root_start = root_end - timedelta(milliseconds=trajectory.get("latency_ms", 0))
        # Create the user-turn root span.
        root = _tracer.start_span(
            "multi_agent_turn",
            context=Context(),
            start_time=int(root_start.timestamp() * 1_000_000_000),
            attributes={
                "openinference.span.kind": "AGENT",
                "openinference.project.name": _project_name or "agents-dev",
                "input.value": _content(user_input),
                "output.value": _content(answer),
                "session.id": trajectory["session_id"],
                "app.turn_id": trajectory["turn_id"],
                "app.trace_id": trajectory["trace_id"],
                "agent.name": "coordinator",
                "agent.depth": 0,
                "app.status": trajectory["status"],
                "llm.token_count.total": trajectory.get("total_tokens", 0),
            },
        )
        # Build an explicit root context for all children.
        root_context = trace.set_span_in_context(root)
        # Create coordinator model spans as children of the complete user turn.
        for event in trajectory.get("events", []):
            # Select only coordinator model operations.
            if event.get("type") != "model_call" or event.get("depth") != 0:
                # Continue scanning.
                continue
            # Create the model span under the turn.
            span = _tracer.start_span(
                f"coordinator.model.{event.get('number', 0)}",
                context=root_context,
                start_time=_start_ns(event),
                attributes={
                    "openinference.span.kind": "LLM",
                    "openinference.project.name": _project_name or "agents-dev",
                    "agent.name": "coordinator",
                    "agent.depth": 0,
                    "llm.model_name": os.getenv("GEMINI_MODEL", ""),
                    "llm.decision": event.get("decision", ""),
                    "llm.tool_names": json.dumps(event.get("tool_names", [])),
                    "llm.token_count.prompt": event.get("tokens", {}).get("input", 0),
                    "llm.token_count.completion": event.get("tokens", {}).get("output", 0),
                    "llm.token_count.total": event.get("tokens", {}).get("total", 0),
                },
            )
            # End at the recorded event completion time.
            span.end(end_time=_epoch_ns(event["timestamp"]))
        # Create delegation and specialist spans before their child operations.
        specialist_spans = {}
        delegation_spans = {}
        # Visit delegation starts.
        for event in trajectory.get("events", []):
            # Ignore other events.
            if event.get("type") != "delegation_start":
                # Continue scanning.
                continue
            # Resolve the matching completion event.
            end_event = delegation_ends.get(event["step_id"])
            # Use turn finish as a defensive fallback.
            end_timestamp = (
                end_event["timestamp"]
                if end_event
                else trajectory["finished_at"]
            )
            # Create the delegation span.
            delegation = _tracer.start_span(
                f"delegate.{event.get('target_agent', 'unknown')}",
                context=root_context,
                start_time=_epoch_ns(event["timestamp"]),
                attributes={
                    "openinference.span.kind": "AGENT",
                    "openinference.project.name": _project_name or "agents-dev",
                    "agent.name": "coordinator",
                    "agent.target": event.get("target_agent", ""),
                    "agent.depth": 0,
                    "app.parent_step_id": event.get("parent_step_id", ""),
                    "input.value": _content(
                        {"task": event.get("task", ""), "context": event.get("context", "")}
                    ),
                },
            )
            # Retain the open delegation for child creation.
            delegation_spans[event["step_id"]] = (delegation, end_timestamp)
            # Skip specialist span when no completion exists.
            if not end_event:
                # Continue with other delegations.
                continue
            # Resolve the specialist result.
            run_id = end_event.get("target_agent_run_id")
            # Read its structured output.
            result = results_by_run.get(run_id, {})
            # Infer specialist start from its own latency.
            specialist_end = datetime.fromisoformat(end_event["timestamp"])
            # Subtract reported duration.
            specialist_start = specialist_end - timedelta(
                milliseconds=end_event.get("latency_ms", 0)
            )
            # Create a specialist agent span under delegation.
            specialist = _tracer.start_span(
                event.get("target_agent", "specialist"),
                context=trace.set_span_in_context(delegation),
                start_time=int(specialist_start.timestamp() * 1_000_000_000),
                attributes={
                    "openinference.span.kind": "AGENT",
                    "openinference.project.name": _project_name or "agents-dev",
                    "agent.name": event.get("target_agent", ""),
                    "agent.run_id": run_id or "",
                    "agent.depth": 1,
                    "output.value": _content(result),
                    "app.status": end_event.get("status", ""),
                },
            )
            # Retain the open specialist for child spans.
            specialist_spans[run_id] = (specialist, end_event["timestamp"])
        # Create specialist model, error, and tool spans.
        for event in trajectory.get("events", []):
            # Select depth-one operational events.
            if event.get("depth") != 1 or event.get("type") not in {
                "model_call",
                "model_error",
                "tool_call",
            }:
                # Continue scanning.
                continue
            # Resolve the specialist parent.
            specialist_entry = specialist_spans.get(event.get("agent_run_id"))
            # Skip malformed orphan events.
            if specialist_entry is None:
                # Continue safely.
                continue
            # Unpack the open parent.
            specialist, _ = specialist_entry
            # Select semantic span kind.
            kind = "TOOL" if event["type"] == "tool_call" else "LLM"
            # Build bounded attributes.
            attributes = {
                "openinference.span.kind": kind,
                "openinference.project.name": _project_name or "agents-dev",
                "agent.name": event.get("agent_name", ""),
                "agent.run_id": event.get("agent_run_id", ""),
                "agent.depth": 1,
                "app.event_type": event["type"],
                "app.status": "error" if event["type"] == "model_error" else "ok",
            }
            # Add tool-specific input and output.
            if event["type"] == "tool_call":
                # Record the validated tool name.
                attributes["tool.name"] = event.get("tool", "")
                # Record bounded arguments.
                attributes["input.value"] = _content(event.get("arguments", {}))
                # Record bounded observation.
                attributes["output.value"] = _content(event.get("result", {}))
            # Add model-specific metadata.
            else:
                # Record the configured model.
                attributes["llm.model_name"] = os.getenv("GEMINI_MODEL", "")
                # Record token counters.
                attributes["llm.token_count.prompt"] = event.get("tokens", {}).get("input", 0)
                attributes["llm.token_count.completion"] = event.get("tokens", {}).get("output", 0)
                attributes["llm.token_count.total"] = event.get("tokens", {}).get("total", 0)
            # Create the operational child span.
            span = _tracer.start_span(
                (
                    f"tool.{event.get('tool', 'unknown')}"
                    if event["type"] == "tool_call"
                    else f"{event.get('agent_name', 'specialist')}.model.{event.get('number', 0)}"
                ),
                context=trace.set_span_in_context(specialist),
                start_time=_start_ns(event),
                attributes=attributes,
            )
            # Mark provider errors.
            if event["type"] == "model_error":
                # Set error status with the recorded diagnostic.
                span.set_status(Status(StatusCode.ERROR, event.get("error", "")))
            # End at recorded completion time.
            span.end(end_time=_epoch_ns(event["timestamp"]))
        # End specialist spans after all children have been created.
        for specialist, end_timestamp in specialist_spans.values():
            # End at the specialist completion time.
            specialist.end(end_time=_epoch_ns(end_timestamp))
        # End delegation spans after specialist spans.
        for delegation, end_timestamp in delegation_spans.values():
            # End at fan-in completion.
            delegation.end(end_time=_epoch_ns(end_timestamp))
        # Set root success or error status.
        root.set_status(
            Status(
                StatusCode.OK if trajectory["status"] == "completed" else StatusCode.ERROR
            )
        )
        # Capture Phoenix/OTEL trace ID before ending.
        phoenix_trace_id = format(root.get_span_context().trace_id, "032x")
        # End the root at the recorded turn completion.
        root.end(end_time=_epoch_ns(trajectory["finished_at"]))
        # Return the cross-system identifier.
        return phoenix_trace_id
    # Telemetry is strictly best-effort.
    except Exception:
        # Signal export failure without changing agent behavior.
        return None
    # Restore the caller's context even after an exporter failure or early return.
    finally:
        # Detach only when the clean context was successfully attached.
        if context_token is not None and detach_context is not None:
            # Return control to the experiment worker's original context.
            detach_context(context_token)
