"""Command-line entry point for learning and trying the agent."""

# Import operating-system environment variables.
import os
# Import UUID generation for a new conversation.
from uuid import uuid4

# Import the official Gemini client.
from google import genai
# Import dotenv so local secrets can live outside source code.
from dotenv import load_dotenv

# Import our single-turn agent runner from the source package.
from src.agent import run_turn

# Load variables from python_agent/.env when it exists.
load_dotenv()


# Keep startup and interactive input separate from import-time behavior.
def main() -> None:
    """Start one persistent multi-turn terminal session."""
    # Read the secret only from the environment.
    api_key = os.getenv("GEMINI_API_KEY")
    # Stop with an actionable message when configuration is missing.
    if not api_key:
        # Raising avoids accidentally sending an unauthenticated request.
        raise RuntimeError("Set GEMINI_API_KEY in .env first.")
    # Use the requested Gemma model unless the environment overrides it.
    model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    # Create the SDK client once and reuse its network connection.
    client = genai.Client(api_key=api_key)
    # Give this terminal conversation one stable session ID.
    session_id = str(uuid4())
    # Show the important runtime identity values.
    print(f"Session: {session_id}")
    # Explain the only local exit command.
    print("Ask a question, or type exit.")
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
        # Run the complete model-tool trajectory.
        result = run_turn(client, model, session_id, user_text)
        # Print the user-facing result.
        print(f"\nAgent: {result['answer']}")
        # Print compact observability information for learning.
        print(
            f"[trace={result['trajectory']['trace_id']} "
            f"tokens={result['trajectory']['total_tokens']} "
            f"latency_ms={result['trajectory']['latency_ms']}]"
        )


# Run the CLI only when this file is executed directly.
if __name__ == "__main__":
    # Invoke the command-line application.
    main()
