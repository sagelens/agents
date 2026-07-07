"""Run the real multi-agent application as a Phoenix experiment."""

# Import command-line parsing for the documented evaluation commands.
import argparse
# Import JSON for the versioned dataset and judge response.
import json
# Import environment configuration.
import os
# Import timestamps for readable experiment names.
from datetime import datetime, timezone
# Import the dataset path helper.
from pathlib import Path
# Import UUIDs so every example receives isolated conversation memory.
from uuid import uuid4

# Import OTEL context controls to suppress traces created by judge calls.
from opentelemetry.context import (
    _SUPPRESS_INSTRUMENTATION_KEY,
    attach,
    detach,
    set_value,
)

# Load the same local .env file used by the interactive agent.
from dotenv import load_dotenv
# Use Phoenix's API client for datasets and experiments.
from phoenix.client import Client

# Run evaluations through the production entry point.
from src.agent import MAX_DELEGATION_ROUNDS, MAX_SPECIALIST_INVOCATIONS, run_turn
# Reuse the specialist registry to verify tool boundaries.
from src.agents import AGENT_SPECS
# Configure a separate trace project for evaluated application runs.
from src.telemetry import configure_telemetry, shutdown_telemetry

# Resolve files independently of the current terminal directory.
DATASET_PATH = Path(__file__).with_name("dataset.jsonl")
# Give every dataset version the same human-readable Phoenix name.
DATASET_NAME = "framework-free-multi-agent-v1"


# Read the small JSON Lines dataset into ordinary dictionaries.
def load_examples(selected_ids: set[str] | None, limit: int | None) -> list[dict]:
    """Return selected examples in their stable file order."""
    # Parse each non-empty line.
    examples = [
        json.loads(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Keep only explicitly requested stable IDs.
    if selected_ids:
        examples = [example for example in examples if example["id"] in selected_ids]
    # Apply the optional quick-run limit last.
    if limit is not None:
        examples = examples[:limit]
    # Reject typos before creating a misleading experiment.
    if selected_ids:
        found = {example["id"] for example in examples}
        missing = selected_ids - found
        if missing:
            raise ValueError(f"Unknown evaluation IDs: {', '.join(sorted(missing))}")
    # Return the selected evaluation suite.
    return examples


# Derive the observable route from delegation events.
def route_summary(trajectory: dict) -> tuple[list[str], str]:
    """Return ordered agents and direct/single/parallel/sequential mode."""
    # Select accepted delegation starts only.
    starts = [
        event
        for event in trajectory["events"]
        if event.get("type") == "delegation_start"
    ]
    # Preserve first-use order while removing duplicates.
    agents = list(dict.fromkeys(event["target_agent"] for event in starts))
    # No specialist means a direct coordinator response.
    if not starts:
        return agents, "direct"
    # One delegation is the single-specialist path.
    if len(starts) == 1:
        return agents, "single"
    # Siblings from one coordinator call are parallel-safe work.
    if len({event.get("parent_step_id") for event in starts}) == 1:
        return agents, "parallel"
    # Delegations from later coordinator calls form a dependency chain.
    return agents, "sequential"


# Convert a full turn into compact experiment output.
def summarize_result(result: dict) -> dict:
    """Return the answer, route, tools, metrics, and trace identifiers."""
    # Read the application's source-of-truth trajectory.
    trajectory = result["trajectory"]
    # Derive specialist order and execution shape.
    agents, mode = route_summary(trajectory)
    # Preserve low-level tool-call order.
    tools = [
        event["tool"]
        for event in trajectory["events"]
        if event.get("type") == "tool_call"
    ]
    # Return only fields useful for comparing experiments.
    return {
        "answer": result["answer"],
        "agents": agents,
        "mode": mode,
        "tools": tools,
        "statuses": [
            {"agent": item["agent"], "status": item["status"]}
            for item in trajectory["agent_summaries"]
        ],
        "artifacts": result.get("artifacts", []),
        "tokens": trajectory["tokens"],
        "latency_ms": trajectory["latency_ms"],
        "status": trajectory["status"],
        "trace_id": trajectory["trace_id"],
        "phoenix_trace_id": result.get("phoenix_trace_id"),
        "delegation_count": len(
            [
                event
                for event in trajectory["events"]
                if event.get("type") == "delegation_start"
            ]
        ),
        "coordinator_calls": len(
            [
                event
                for event in trajectory["events"]
                if event.get("type") == "model_call"
                and event.get("agent_name") == "coordinator"
            ]
        ),
    }


# Build one production task closure for Phoenix.
def make_task(api_key: str, model: str):
    """Return a Phoenix task that invokes the real run_turn function."""
    # Phoenix supplies inputs, expected output, and metadata by name.
    def task(input: dict) -> dict:
        # Use a fresh session so dataset examples never share memory.
        result = run_turn(api_key, model, str(uuid4()), input["prompt"])
        # Store a compact, comparable experiment output.
        return summarize_result(result)

    # Return the callable to the experiment runner.
    return task


# Return deterministic score objects without raising on failures.
def deterministic_evaluator(output: dict, expected: dict) -> list[dict]:
    """Score routing, boundaries, limits, artifacts, and efficiency."""
    # Read expected and observed routes.
    expected_agents = expected.get("agents", [])
    actual_agents = output.get("agents", [])
    # Build each specialist's permitted-tool set.
    allowed_by_agent = {
        name: set(spec.tool_names)
        for name, spec in AGENT_SPECS.items()
    }
    # Combine permitted tools for the agents actually selected.
    allowed_tools = set().union(
        *(allowed_by_agent.get(agent, set()) for agent in actual_agents)
    ) if actual_agents else set()
    # Check any required artifact type.
    required_media = expected.get("artifact_media_type")
    artifact_ok = required_media is None or any(
        artifact.get("media_type") == required_media
        for artifact in output.get("artifacts", [])
    )
    # Count repeated specialist selections.
    redundant = len(actual_agents) - len(set(actual_agents))
    # Build named Phoenix evaluation records.
    return [
        {"name": "specialist_selection", "score": float(actual_agents == expected_agents)},
        {"name": "execution_mode", "score": float(output.get("mode") == expected.get("mode"))},
        {"name": "tool_boundary", "score": float(set(output.get("tools", [])) <= allowed_tools)},
        {"name": "loop_limits", "score": float(output.get("delegation_count", 0) <= MAX_SPECIALIST_INVOCATIONS and output.get("coordinator_calls", 0) <= MAX_DELEGATION_ROUNDS + 1)},
        {"name": "completion", "score": float(output.get("status") == "completed"), "label": output.get("status")},
        {"name": "artifact_requirement", "score": float(artifact_ok)},
        {"name": "path_efficiency", "score": 1.0 if not actual_agents else min(1.0, len(expected_agents) / max(1, output.get("delegation_count", 0)))},
        {"name": "redundant_delegations", "score": float(redundant), "direction": "minimize"},
        {"name": "tokens", "score": float(output.get("tokens", {}).get("total", 0)), "direction": "neutral"},
        {"name": "latency_ms", "score": float(output.get("latency_ms", 0)), "direction": "neutral"},
    ]


# Create an optional Phoenix Evals-backed Gemma judge.
def make_judge(model: str):
    """Return an evaluator that asks Gemma for bounded qualitative scores."""
    # Import the optional evaluator only when explicitly requested.
    from phoenix.evals import LLM
    # Configure Phoenix Evals' Google adapter with the existing environment key.
    judge = LLM(provider="google", model=model)

    # Phoenix passes actual and expected values by parameter name.
    def gemma_judge(output: dict, expected: dict) -> list[dict]:
        # Ask for machine-readable scores covering the requested agent dimensions.
        prompt = (
            "You are evaluating an AI agent run. Return JSON only with keys "
            "selection, delegation_quality, tool_handling, correctness, "
            "citation_and_honesty, conciseness. Each value must be 0 or 1.\n"
            f"Expected: {json.dumps(expected, ensure_ascii=False)}\n"
            f"Observed: {json.dumps(output, ensure_ascii=False)[:12000]}"
        )
        # Suppress the judge's own model span to avoid recursive evaluation traces.
        token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
        try:
            # Generate one qualitative judgment.
            raw = judge.generate_text(prompt)
        finally:
            # Restore the experiment worker's prior OTEL context.
            detach(token)
        # Tolerate fenced JSON from an instruction-following model.
        cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
        # Parse the six requested dimensions.
        scores = json.loads(cleaned)
        # Convert each item into a named Phoenix score.
        return [
            {"name": f"judge_{name}", "score": float(value), "explanation": "Gemma LLM-as-judge"}
            for name, value in scores.items()
        ]

    # Return the optional evaluator.
    return gemma_judge


def append_example(example: dict) -> dict:
    """Validate and atomically append one user-authored evaluation example."""
    required = {"id", "prompt", "expected"}
    if not required <= set(example):
        raise ValueError("Example requires id, prompt, and expected.")
    example_id = str(example["id"]).strip()
    if not example_id or not example_id.replace("_", "").isalnum():
        raise ValueError("Example ID may contain only letters, numbers, and underscores.")
    prompt = str(example["prompt"]).strip()
    expected = example["expected"]
    if not prompt or not isinstance(expected, dict):
        raise ValueError("Prompt must be non-empty and expected must be an object.")
    if expected.get("mode") not in {"direct", "single", "parallel", "sequential"}:
        raise ValueError("Expected mode must be direct, single, parallel, or sequential.")
    for field in ("agents", "tools"):
        if not isinstance(expected.get(field, []), list):
            raise ValueError(f"Expected {field} must be an array.")
    existing = load_examples(None, None)
    if any(item["id"] == example_id for item in existing):
        raise ValueError(f"Evaluation ID already exists: {example_id}")
    normalized = {
        "id": example_id,
        "prompt": prompt[:8_000],
        "expected": expected,
        "metadata": example.get("metadata", {}),
    }
    temporary = DATASET_PATH.with_suffix(".tmp")
    lines = [
        json.dumps(item, ensure_ascii=False)
        for item in [*existing, normalized]
    ]
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(DATASET_PATH)
    return normalized


def run_evaluation(
    selected_ids: set[str] | None = None,
    limit: int | None = None,
    judge_enabled: bool = False,
) -> dict:
    """Run the Phoenix experiment pipeline and return a UI-friendly summary."""
    # Load project-local configuration.
    load_dotenv()
    # Validate the existing Gemini configuration.
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in .env before running evaluations.")
    # Resolve application and judge models.
    model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    judge_model = os.getenv("PHOENIX_EVAL_MODEL", model)
    # Load the requested stable examples.
    examples = load_examples(selected_ids, limit)
    # Connect to local Phoenix.
    base_url = os.getenv("PHOENIX_BASE_URL", "http://localhost:6006")
    client = Client(base_url=base_url)
    # Convert the readable file format into Phoenix's input/output envelope.
    phoenix_examples = [
        {
            "id": example["id"],
            "input": {"prompt": example["prompt"]},
            "output": example["expected"],
            "metadata": {"suite": DATASET_NAME},
        }
        for example in examples
    ]
    # Upload a version whose stable IDs permit comparison across runs.
    dataset = client.datasets.create_dataset(
        name=DATASET_NAME,
        examples=phoenix_examples,
        dataset_description=(
            f"{len(phoenix_examples)} routing, tool, dependency, artifact, "
            "robustness, grounding, and safety examples."
        ),
    )
    # Export evaluated application traces to their separate project.
    os.environ["PHOENIX_ENABLED"] = "true"
    configure_telemetry(os.getenv("PHOENIX_EVAL_PROJECT", "agents-evals"))
    # Always include deterministic report-only scoring.
    evaluators = [deterministic_evaluator]
    # Add an explicitly requested, potentially biased LLM judge.
    if judge_enabled:
        evaluators.append(make_judge(judge_model))
    # Use a timestamp so repeated runs remain directly comparable.
    experiment_name = "agents-eval-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        # Let Phoenix persist task outputs and every evaluator score.
        client.experiments.run_experiment(
            dataset=dataset,
            task=make_task(api_key, model),
            evaluators=evaluators,
            experiment_name=experiment_name,
            experiment_description="Framework-free multi-agent routing and quality evaluation.",
            experiment_metadata={"judge_enabled": judge_enabled, "model": model},
            print_summary=True,
            retries=0,
            timeout=120,
        )
    finally:
        # Flush evaluated application traces even when an evaluator errors.
        shutdown_telemetry()
    return {
        "kind": "evaluation",
        "status": "completed",
        "experiment_name": experiment_name,
        "dataset_name": DATASET_NAME,
        "example_count": len(examples),
        "selected_ids": [example["id"] for example in examples],
        "judge_enabled": judge_enabled,
        "model": model,
        "phoenix_url": base_url,
    }


# Parse commands and execute one Phoenix experiment.
def main() -> None:
    """Upload a dataset version and run the selected examples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="Run only the first N selected examples.")
    parser.add_argument("--ids", help="Comma-separated stable example IDs.")
    parser.add_argument("--judge", action="store_true", help="Add Gemma qualitative evaluators.")
    args = parser.parse_args()
    result = run_evaluation(
        selected_ids=set(args.ids.split(",")) if args.ids else None,
        limit=args.limit,
        judge_enabled=args.judge,
    )
    print(
        f"\nPhoenix: {result['phoenix_url']}  "
        f"Experiment: {result['experiment_name']}"
    )


# Execute only for python -m evals.run.
if __name__ == "__main__":
    # Start the offline evaluation command.
    main()
