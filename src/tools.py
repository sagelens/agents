"""Small, independently understandable implementations of the agent's tools."""

# Import JSON to decode ripgrep's machine-readable output.
import json
# Import environment variables for the configured codebase root.
import os
# Import safe path operations for preventing directory traversal.
from pathlib import Path
# Import executable discovery so a missing ripgrep has a useful error.
import shutil
# Import process control without invoking a command shell.
import subprocess

# Import the current DuckDuckGo search client.
from ddgs import DDGS
# Import requests for readable HTTP calls.
import requests

# Limit every network call so a broken service cannot freeze the agent forever.
TIMEOUT_SECONDS = 15

# Never allow the code-search tool to inspect common secret or generated paths.
DENIED_PATH_NAMES = {".env", ".git", ".venv", "node_modules", "data"}

# Pass these exclusions directly to ripgrep as a second layer of protection.
DENIED_GLOBS = [
    "!**/.env",
    "!**/.env.*",
    "!**/.git/**",
    "!**/.venv/**",
    "!**/node_modules/**",
    "!**/data/**",
    "!**/*.pem",
    "!**/*.key",
    "!**/*.p12",
    "!**/*.db",
    "!**/*.sqlite",
]


# Define the weather function that the model is allowed to request.
def get_weather(location: str) -> dict:
    """Get current weather for a city or place.

    Args:
        location: Human-readable place, such as "Delhi" or "Paris, France".

    Returns:
        A JSON-compatible dictionary containing current weather data.
    """
    # Reject an empty location before making a network request.
    if not location.strip():
        # Return an error as data so the agent can explain it.
        return {"ok": False, "error": "A location is required."}
    # Ask Open-Meteo's geocoder to translate the place into coordinates.
    geocoding_response = requests.get(
        # Use Open-Meteo's public geocoding endpoint.
        "https://geocoding-api.open-meteo.com/v1/search",
        # Send user input as a query parameter so requests escapes it safely.
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        # Apply the shared network timeout.
        timeout=TIMEOUT_SECONDS,
    )
    # Raise a clear exception for HTTP failures such as 500 responses.
    geocoding_response.raise_for_status()
    # Convert the JSON response body into a Python dictionary.
    geocoding_data = geocoding_response.json()
    # Read the list defensively because an unknown place returns no results.
    places = geocoding_data.get("results", [])
    # Stop if Open-Meteo could not identify the requested place.
    if not places:
        # Return a safe observation instead of guessing coordinates.
        return {"ok": False, "error": f"Location not found: {location}"}
    # Select the highest-ranked matching location.
    place = places[0]
    # Request current weather for the resolved latitude and longitude.
    weather_response = requests.get(
        # Use Open-Meteo's forecast endpoint, which also supplies current values.
        "https://api.open-meteo.com/v1/forecast",
        # Ask only for the small set of values this teaching agent needs.
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        # Apply the same network timeout.
        timeout=TIMEOUT_SECONDS,
    )
    # Convert non-success HTTP responses into exceptions.
    weather_response.raise_for_status()
    # Read the current-weather object from the JSON response.
    current = weather_response.json().get("current", {})
    # Return only JSON-compatible, useful values to the model.
    return {
        "ok": True,
        "location": place.get("name"),
        "country": place.get("country"),
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "precipitation_mm": current.get("precipitation"),
        "weather_code": current.get("weather_code"),
        "wind_kmh": current.get("wind_speed_10m"),
    }


# Define the general web-search function exposed to the model.
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the public web for current information.

    Args:
        query: A focused search-engine query.
        max_results: Number of results to return, from 1 through 5.

    Returns:
        A JSON-compatible dictionary of result titles, URLs, and snippets.
    """
    # Reject an empty query instead of sending meaningless traffic.
    if not query.strip():
        # Return a structured error that the agent can understand.
        return {"ok": False, "error": "A search query is required."}
    # Constrain model-provided input to a small and predictable result count.
    safe_limit = max(1, min(int(max_results), 5))
    # Execute a text search and materialize its generator into a list.
    raw_results = list(DDGS().text(query, max_results=safe_limit))
    # Keep only fields useful for answering and citing the result.
    results = [
        # Normalize each provider result into our stable tool contract.
        {"title": item.get("title"), "url": item.get("href"), "snippet": item.get("body")}
        # Transform every returned search result.
        for item in raw_results
    ]
    # Return a predictable object even when the result list is empty.
    return {"ok": True, "query": query, "results": results}


# Define the read-only repository search function exposed to the model.
def grep_code(
    pattern: str,
    path: str = ".",
    file_glob: str = "",
    context_lines: int = 2,
    max_matches: int = 12,
    fixed_string: bool = False,
) -> dict:
    """Search code inside the configured repository using ripgrep.

    Args:
        pattern: Text or regular expression to locate.
        path: Relative file or directory beneath CODEBASE_ROOT.
        file_glob: Optional file filter such as "*.py".
        context_lines: Number of surrounding lines, from 0 through 3.
        max_matches: Maximum matching lines to return, from 1 through 12.
        fixed_string: Treat the pattern literally instead of as a regular expression.

    Returns:
        A JSON-compatible dictionary containing matching source snippets.
    """
    # Reject empty searches before starting a process.
    if not pattern.strip():
        # Return an actionable structured error.
        return {"ok": False, "error": "A non-empty search pattern is required."}
    # Resolve the only directory the tool is permitted to inspect.
    codebase_root = Path(os.getenv("CODEBASE_ROOT", Path.cwd())).expanduser().resolve()
    # Resolve the model-provided relative path against the trusted root.
    requested_path = (codebase_root / path).resolve()
    # Reject absolute paths and traversal that resolve outside the trusted root.
    try:
        # This succeeds only when the requested path is beneath the root.
        relative_path = requested_path.relative_to(codebase_root)
    # Catch the path-containment failure.
    except ValueError:
        # Do not reveal or inspect the escaped destination.
        return {"ok": False, "error": "The requested path is outside CODEBASE_ROOT."}
    # Reject nonexistent paths with a clear observation.
    if not requested_path.exists():
        # Return the safe relative path rather than an absolute machine path.
        return {"ok": False, "error": f"Path does not exist: {relative_path}"}
    # Inspect each path component for explicitly denied directories or files.
    if any(part in DENIED_PATH_NAMES or part.startswith(".env") for part in requested_path.parts):
        # Refuse direct requests even though ripgrep also receives exclusion globs.
        return {"ok": False, "error": "The requested path is protected from code search."}
    # Locate ripgrep without consulting or executing a command shell.
    ripgrep = shutil.which("rg")
    # Explain the system dependency when it is unavailable.
    if not ripgrep:
        # Suggest the standard macOS installation command as data.
        return {"ok": False, "error": "ripgrep is not installed; run: brew install ripgrep"}
    # Clamp every model-controlled numeric value.
    safe_context = max(0, min(int(context_lines), 3))
    # Cap returned evidence to control latency and model token usage.
    safe_limit = max(1, min(int(max_matches), 12))
    # Begin a shell-free argument list with deterministic ripgrep behavior.
    command = [
        ripgrep,
        "--json",
        "--no-config",
        "--no-follow",
        "--max-depth=8",
        "--max-filesize=1M",
        "--max-columns=300",
        f"--context={safe_context}",
    ]
    # Treat the search pattern literally when the model requests fixed-string mode.
    if fixed_string:
        # Add ripgrep's fixed-string flag.
        command.append("--fixed-strings")
    # Apply the optional user-facing file filter.
    if file_glob.strip():
        # Add the glob as a separate argument so it cannot become another option.
        command.extend(["--glob", file_glob])
    # Apply every mandatory exclusion.
    for denied_glob in DENIED_GLOBS:
        # Add each glob as its own argument.
        command.extend(["--glob", denied_glob])
    # End option parsing before adding model-controlled pattern and path values.
    command.extend(["--", pattern, str(requested_path)])
    # Collect both matches and surrounding context in chronological output order.
    snippets = []
    # Track matching lines separately because context lines do not count toward the cap.
    match_count = 0
    # Remember whether Python stopped ripgrep after reaching the global result limit.
    truncated = False
    # Start ripgrep with no shell and line-buffered text output.
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # Parse each JSON event as ripgrep emits it.
    try:
        # The assertion helps type checkers understand that stdout is available.
        assert process.stdout is not None
        # Iterate without first loading unbounded output into memory.
        for output_line in process.stdout:
            # Decode one ripgrep event.
            event = json.loads(output_line)
            # Keep only matching and contextual source lines.
            if event.get("type") not in {"match", "context"}:
                # Ignore begin, end, and summary events.
                continue
            # Extract the event's nested data object.
            data = event["data"]
            # Convert the absolute result path back to a safe repository-relative path.
            result_path = Path(data["path"]["text"]).resolve().relative_to(codebase_root)
            # Mark actual matches separately from surrounding context.
            kind = event["type"]
            # Add one compact source snippet.
            snippets.append(
                {
                    "kind": kind,
                    "file": str(result_path),
                    "line": data.get("line_number"),
                    "text": data["lines"]["text"].rstrip("\r\n"),
                }
            )
            # Increment the global count only for actual matches.
            if kind == "match":
                # Count this matching source line.
                match_count += 1
            # Stop producing output after the configured global match limit.
            if match_count >= safe_limit:
                # Record that more results may exist.
                truncated = True
                # Ask ripgrep to stop cleanly.
                process.terminate()
                # Leave the streaming loop immediately.
                break
        # Wait briefly for normal completion or termination.
        process.wait(timeout=TIMEOUT_SECONDS)
    # Handle a search that exceeds the total execution deadline.
    except subprocess.TimeoutExpired:
        # Force-stop the read-only process.
        process.kill()
        # Return a structured timeout error.
        return {"ok": False, "error": f"Code search exceeded {TIMEOUT_SECONDS} seconds."}
    # Handle malformed regexes or unexpected non-JSON output safely.
    except (json.JSONDecodeError, KeyError, ValueError) as error:
        # Stop the child before returning.
        process.kill()
        # Describe the parsing failure without executing anything else.
        return {"ok": False, "error": f"Could not parse code-search results: {error}"}
    # Read the small diagnostic stream after the process has stopped.
    stderr = process.stderr.read().strip() if process.stderr else ""
    # Ripgrep exit code 2 represents an invalid regex, path, or runtime error.
    if process.returncode not in {0, 1, -15} and not truncated:
        # Return ripgrep's diagnostic for the model to explain.
        return {"ok": False, "error": stderr or "ripgrep failed."}
    # Return structured, bounded repository evidence.
    return {
        "ok": True,
        "pattern": pattern,
        "searched_path": str(relative_path) or ".",
        "file_glob": file_glob or None,
        "match_count": match_count,
        "matching_files": sorted(
            {snippet["file"] for snippet in snippets if snippet["kind"] == "match"}
        ),
        "truncated": truncated,
        "snippets": snippets,
    }


# Map model-visible names to the only Python functions it may execute.
TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "web_search": web_search,
    "grep_code": grep_code,
}

# Describe tool arguments using the JSON Schema subset accepted by Gemini.
TOOL_DECLARATIONS = [
    # Declare the weather tool.
    {
        "name": "get_weather",
        "description": "Get live current weather for a city or named place.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place, optionally including state and country.",
                }
            },
            "required": ["location"],
        },
    },
    # Declare the web-search tool.
    {
        "name": "web_search",
        "description": "Search the public web for current or uncertain information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Focused search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Number of results, from 1 through 5.",
                },
            },
            "required": ["query"],
        },
    },
    # Declare the read-only repository search tool.
    {
        "name": "grep_code",
        "description": "Search source code inside the configured repository and return matching lines with small context.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regular expression to find in source code.",
                },
                "path": {
                    "type": "string",
                    "description": "Relative file or directory beneath the configured repository root.",
                },
                "file_glob": {
                    "type": "string",
                    "description": "Optional filter such as *.py or src/**/*.ts.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Surrounding source lines, from 0 through 3.",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Maximum matching lines, from 1 through 12.",
                },
                "fixed_string": {
                    "type": "boolean",
                    "description": "Search literally instead of interpreting a regular expression.",
                },
            },
            "required": ["pattern"],
        },
    },
]
