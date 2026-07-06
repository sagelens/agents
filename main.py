"""Command-line entry point for learning and trying the agent."""

# Import operating-system environment variables.
import os
import json
import sys
from pathlib import Path
# Import UUID generation for a new conversation.
from uuid import uuid4

# Import dotenv so local secrets can live outside source code.
from dotenv import load_dotenv

# Import our single-turn agent runner from the source package.
from src.agent import run_turn
from src.orchestrator import run_workflow
from src.outreach import (
    OutreachRegistry,
    authorize_email,
    email_auth_status,
    run_outreach,
)
from src.resume_screening import run_resume_screening
from src.resume_security import save_security_override
from src.google_workspace import GoogleWorkspace
# Import optional Phoenix lifecycle helpers.
from src.telemetry import configure_telemetry, shutdown_telemetry
from src.deep_research import (
    pending_checkpoint,
    resume_deep_research,
    select_research_direction,
    start_deep_research,
)
from src.gui import serve_gui

# Load variables from the project .env when it exists.
load_dotenv()


# Keep startup and interactive input separate from import-time behavior.
def cli_main() -> None:
    """Start one persistent multi-turn terminal session."""
    # Read the secret only from the environment.
    api_key = os.getenv("GEMINI_API_KEY")
    # Stop with an actionable message when configuration is missing.
    if not api_key:
        # Raising avoids accidentally sending an unauthenticated request.
        raise RuntimeError("Set GEMINI_API_KEY in .env first.")
    # Use the requested Gemma model unless the environment overrides it.
    model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    # Configure optional local Phoenix tracing.
    configure_telemetry()
    # Give this terminal conversation one stable session ID.
    session_id = str(uuid4())
    # Show the important runtime identity values.
    print(f"Session: {session_id}")
    # Explain the only local exit command.
    print(
        "Ask a question, type exit, use /workflow path/to/workflow.json, "
        "use /research <query>, /research-resume <run-id>, /screen-resumes, "
        "/resume-security-review, or /outreach-shortlisted."
    )
    # Continue accepting user turns until explicitly stopped.
    while True:
        # Read one user message and remove surrounding whitespace.
        user_text = input("\nYou: ").strip()
        # End cleanly for either common exit spelling.
        if user_text.lower() in {"exit", "quit"}:
            # Leave the loop and finish the process.
            break
        # Ignore empty terminal submissions.
        if not user_text:
            # Return to the prompt without calling the model.
            continue
        if user_text.startswith("/research ") or user_text.startswith(
            "/research-resume "
        ):
            try:
                if user_text.startswith("/research-resume "):
                    run_id = user_text.removeprefix("/research-resume ").strip()
                    research = resume_deep_research(run_id)
                else:
                    query = user_text.removeprefix("/research ").strip()
                    research = start_deep_research(api_key, model, query)
                while research["status"] == "waiting_for_human":
                    checkpoint = pending_checkpoint(research)
                    if checkpoint is None:
                        break
                    print(f"\nResearch directions [run={research['run_id']}]:")
                    for index, option in enumerate(checkpoint["options"], start=1):
                        print(f"{index}. {option['label']} — {option['outcome']}")
                    choice = input("Select one direction number (or type exit): ").strip()
                    if choice.lower() in {"exit", "quit"}:
                        print(f"Research paused. Resume with /research-resume {research['run_id']}")
                        break
                    try:
                        option_number = int(choice)
                    except ValueError:
                        print("Enter one of the displayed direction numbers.")
                        continue
                    feedback = input(
                        "Optional guidance or correction (press Enter to skip): "
                    ).strip()
                    research = select_research_direction(
                        api_key, model, research, option_number, feedback
                    )
                if research["status"] == "completed":
                    print(f"\n{research['final_report']}")
                    print(f"\n[research_run={research['run_id']}]")
            except Exception as error:
                print(f"\nResearch failed: {error}")
            continue
        # Execute an explicit DAG definition from a readable JSON file.
        if user_text.startswith("/workflow "):
            workflow_path = Path(user_text.removeprefix("/workflow ").strip()).expanduser()
            definition = json.loads(workflow_path.read_text(encoding="utf-8"))

            def approve(request: dict) -> dict:
                print(f"\nApproval required: {request['action']}")
                print(f"Reason: {request['reason']}")
                choice = input("Approve? [y/N]: ").strip().lower()
                return {
                    "decision": "approved" if choice in {"y", "yes"} else "rejected",
                    "note": "Terminal human decision.",
                }

            workflow_result = run_workflow(
                api_key, model, definition, approval_provider=approve
            )
            print(
                f"\nWorkflow: {workflow_result['workflow']['status']} "
                f"[run={workflow_result['run_id']}]"
            )
            for node_id, node_result in workflow_result["results"].items():
                print(f"- {node_id}: {node_result['status']}")
                if node_result.get("output") is not None:
                    print(json.dumps(node_result["output"], indent=2, ensure_ascii=False))
            continue
        if user_text == "/screen-resumes":
            print("\nScreening PDF resumes from the configured Drive folder...")
            screening = run_resume_screening(api_key, model)
            counts = screening["counts"]
            print(
                "Resume screening complete: "
                f"discovered={counts['discovered']} "
                f"skipped={counts['skipped']} "
                f"screened={counts['screened']} "
                f"shortlisted={counts['shortlisted']} "
                f"not_shortlisted={counts['not_shortlisted']} "
                f"review_required={counts['review_required']} "
                f"errors={counts['errors']}"
            )
            print(
                f"[run={screening['run_id']} "
                f"rubric={screening['rubric_version']} "
                f"threshold={screening['threshold']}]"
            )
            continue
        if user_text == "/resume-security-review":
            rows = GoogleWorkspace().get_values("'Screened_Candidates'!A2:AH")
            flagged = []
            for row_number, row in enumerate(rows, start=2):
                row = list(row) + [""] * (34 - len(row))
                if row[29] in {
                    "INJECTION_REVIEW_REQUIRED",
                    "SECURITY_ANALYSIS_ERROR",
                }:
                    flagged.append(
                        {
                            "row": row_number,
                            "file_id": row[2],
                            "file_name": row[3],
                            "modified_time": row[4],
                            "status": row[29],
                            "reason_codes": row[30],
                        }
                    )
            if not flagged:
                print("\nNo resumes are awaiting security review.")
                continue
            reviewer = os.getenv("SENDER_DISPLAY_NAME", "").strip() or os.getenv(
                "USER", "local-reviewer"
            )
            for item in flagged:
                print("\n--- Resume security review ---")
                print(f"Sheet row: {item['row']}")
                print(f"File: {item['file_name']}")
                print(f"Drive ID: {item['file_id']}")
                print(f"Status: {item['status']}")
                print(f"Reason codes: {item['reason_codes']}")
                choice = input(
                    "Allow, quarantine, replace, or skip? [a/q/r/S]: "
                ).strip().lower()
                decision = {
                    "a": "allow",
                    "allow": "allow",
                    "q": "quarantine",
                    "quarantine": "quarantine",
                    "r": "replace",
                    "replace": "replace",
                }.get(choice)
                if not decision:
                    print("Skipped.")
                    continue
                save_security_override(
                    item["file_id"],
                    item["modified_time"],
                    decision,
                    reviewer,
                )
                print(f"Recorded decision: {decision}")
            print("Run /screen-resumes to process any allowed files.")
            continue
        if user_text == "/authorize-email":
            try:
                result = authorize_email()
                print(f"\nEmail authorized for: {result['email']}")
            except Exception as error:
                print(f"\nEmail authorization failed: {error}")
            continue
        if user_text == "/email-auth-status":
            result = email_auth_status()
            if result["authorized"]:
                print(f"\nEmail authorized for: {result['email']}")
            else:
                print(f"\nEmail is not authorized: {result['error']}")
            continue
        if user_text == "/outreach-status":
            counts = OutreachRegistry(GoogleWorkspace()).status_counts()
            print("\nOutreach status:")
            if not counts:
                print("- No shortlisted candidates with email addresses.")
            for status, count in sorted(counts.items()):
                print(f"- {status}: {count}")
            continue
        if user_text in {"/outreach-shortlisted", "/outreach-shortlisted --resume"}:
            def review_email(draft: dict, approval: dict) -> dict:
                print("\n--- Outreach approval required ---")
                print(f"Approval: {approval['approval_id']}")
                print(f"From: {draft['from']}")
                print(f"To: {draft['to']}")
                print(f"Reply-To: {draft['reply_to']}")
                print(f"Subject: {draft['subject']}")
                print("\n" + draft["text_body"])
                choice = input("\nApprove, edit, or reject? [a/e/R]: ").strip().lower()
                if choice in {"a", "approve"}:
                    return {"decision": "approved"}
                if choice in {"e", "edit"}:
                    subject = input("New subject (blank keeps current): ").strip()
                    print("Enter the complete new body. End with a line containing only a dot.")
                    lines = []
                    while True:
                        line = input()
                        if line == ".":
                            break
                        lines.append(line)
                    return {
                        "decision": "edited",
                        "subject": subject,
                        "text_body": "\n".join(lines),
                    }
                return {"decision": "rejected"}

            outreach = run_outreach(
                api_key,
                model,
                review_email,
                resume=user_text.endswith("--resume"),
            )
            counts = outreach["counts"]
            print(
                "\nOutreach complete: "
                f"eligible={counts['eligible']} "
                f"sent={counts['sent']} "
                f"rejected={counts['rejected']} "
                f"failed={counts['failed']} "
                f"skipped={counts['skipped']}"
            )
            print(f"[run={outreach['run_id']} sender={outreach['sender']}]")
            continue
        # Run the complete model-tool trajectory.
        result = run_turn(api_key, model, session_id, user_text)
        # Print the user-facing result.
        print(f"\nAgent: {result['answer']}")
        # Collect trusted artifact paths from the multi-agent turn result.
        artifact_paths = [
            # Read each validated host path.
            artifact["path"]
            # Visit aggregated specialist artifacts.
            for artifact in result.get("artifacts", [])
            # Require a host path before displaying it.
            if artifact.get("path")
        ]
        # Display usable files independently from model-generated prose.
        if artifact_paths:
            # Add a compact heading after the answer.
            print("\nArtifacts:")
            # Print each absolute path on its own line.
            for artifact_path in artifact_paths:
                # Prefix paths for easy scanning in the terminal.
                print(f"- {artifact_path}")
        # Print compact observability information for learning.
        print(
            f"[trace={result['trajectory']['trace_id']} "
            f"tokens={result['trajectory']['total_tokens']} "
            f"latency_ms={result['trajectory']['latency_ms']}]"
        )


def main() -> None:
    """Start the local GUI by default, or preserve the terminal with --cli."""
    if "--cli" in sys.argv:
        cli_main()
        return
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in .env first.")
    configure_telemetry()
    try:
        serve_gui(port=int(os.getenv("AGENT_GUI_PORT", "9999")))
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
