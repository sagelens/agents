"""Small, independently understandable implementations of the agent's tools."""

# Import JSON to decode ripgrep's machine-readable output.
import json
# Import environment variables for the configured codebase root.
import os
# Import safe path operations for preventing directory traversal.
from pathlib import Path
# Import selectors to enforce output and time limits while reading two pipes.
import selectors
# Import executable discovery so a missing ripgrep has a useful error.
import shutil
# Import process control without invoking a command shell.
import subprocess
# Import monotonic and wall clocks for execution deadlines and artifact retention.
import time
# Import UUID generation for isolated container and artifact names.
from uuid import uuid4

# Import the current DuckDuckGo search client.
from ddgs import DDGS
# Import requests for readable HTTP calls.
import requests

# Limit every network call so a broken service cannot freeze the agent forever.
TIMEOUT_SECONDS = 15

# Resolve generated artifacts beneath the project root.
ARTIFACTS_DIRECTORY = Path(__file__).parent.parent / "artifacts"
# Limit generated source code before it reaches Docker.
MAX_CODE_BYTES = 20 * 1024
# Limit combined stdout and stderr retained from generated code.
MAX_OUTPUT_BYTES = 64 * 1024
# Limit the number of files copied out of one sandbox run.
MAX_ARTIFACTS = 5
# Limit all artifacts from one run to five megabytes.
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
# Keep recent artifacts for at most one week.
ARTIFACT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
# Keep no more than the latest fifty artifact runs.
MAX_ARTIFACT_RUNS = 50
# Permit only simple data and image result formats.
ALLOWED_ARTIFACT_TYPES = {
    ".png": "image/png",
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
}

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


# Remove expired artifact directories before creating another run.
def _prune_artifacts() -> None:
    """Keep artifacts for seven days and retain no more than fifty runs."""
    # Create the root lazily on the first Python execution.
    ARTIFACTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    # Read the current wall clock once for consistent retention decisions.
    current_time = time.time()
    # Collect only directories because each run owns one directory.
    run_directories = [path for path in ARTIFACTS_DIRECTORY.iterdir() if path.is_dir()]
    # Remove directories older than the configured maximum age.
    for path in run_directories:
        # Compare the directory modification time with the retention boundary.
        if current_time - path.stat().st_mtime > ARTIFACT_MAX_AGE_SECONDS:
            # Delete the complete expired run.
            shutil.rmtree(path, ignore_errors=True)
    # Re-read remaining runs and sort newest first.
    remaining = sorted(
        (path for path in ARTIFACTS_DIRECTORY.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    # Delete every run beyond the newest configured count.
    for path in remaining[MAX_ARTIFACT_RUNS:]:
        # Remove the complete old run.
        shutil.rmtree(path, ignore_errors=True)


# Force-remove a named container after a host-side limit is reached.
def _remove_container(docker: str, container_name: str) -> None:
    """Best-effort cleanup that never hides the original execution result."""
    # Prevent cleanup failures from replacing the original sandbox status.
    try:
        # Ask Docker to kill and remove the unique ephemeral container.
        subprocess.run(
            # Pass every value as a separate argument; no shell parses generated data.
            [docker, "rm", "--force", container_name],
            # Discard cleanup output because it may only report that --rm already acted.
            stdout=subprocess.DEVNULL,
            # Discard cleanup diagnostics for the same reason.
            stderr=subprocess.DEVNULL,
            # Do not let cleanup block the agent indefinitely.
            timeout=5,
            # A missing container is harmless.
            check=False,
        )
    # Ignore a stalled or unavailable daemon during best-effort cleanup.
    except (OSError, subprocess.TimeoutExpired):
        # The caller still reports the original timeout or output-limit status.
        pass


# Inspect and normalize files written to the isolated output mount.
def _collect_artifacts(run_directory: Path) -> tuple[list[dict], str | None]:
    """Return safe artifact metadata and an optional artifact-limit error."""
    # Start with an empty accepted-file list.
    accepted_files = []
    # Visit files recursively so generated code may use small subdirectories.
    for path in run_directory.rglob("*"):
        # Remove symlinks rather than following model-generated filesystem references.
        if path.is_symlink():
            # Delete only the link inside the dedicated run directory.
            path.unlink(missing_ok=True)
            # Continue without exposing it.
            continue
        # Ignore directories.
        if not path.is_file():
            # Move to the next entry.
            continue
        # Read the lowercase extension for the allowlist.
        extension = path.suffix.lower()
        # Delete unsupported file formats from the output directory.
        if extension not in ALLOWED_ARTIFACT_TYPES:
            # Remove the unsupported output.
            path.unlink(missing_ok=True)
            # Do not include it in metadata.
            continue
        # Keep the validated file for aggregate checks.
        accepted_files.append(path)
    # Reject a run that creates too many allowed files.
    if len(accepted_files) > MAX_ARTIFACTS:
        # Return a stable error code without preserving a partial selection.
        return [], f"Generated more than {MAX_ARTIFACTS} allowed artifacts."
    # Calculate aggregate size after filtering unsupported files.
    total_size = sum(path.stat().st_size for path in accepted_files)
    # Reject a run that exceeds the total artifact-size boundary.
    if total_size > MAX_ARTIFACT_BYTES:
        # Return a stable explanation.
        return [], f"Artifacts exceeded {MAX_ARTIFACT_BYTES} bytes."
    # Build user-facing metadata in deterministic path order.
    artifacts = [
        {
            # Return a path relative to this run directory as the logical name.
            "name": str(path.relative_to(run_directory)),
            # Return the absolute host path so the terminal user can open it.
            "path": str(path.resolve()),
            # Derive the media type only from the extension allowlist.
            "media_type": ALLOWED_ARTIFACT_TYPES[path.suffix.lower()],
            # Report the actual on-disk byte size.
            "size_bytes": path.stat().st_size,
        }
        # Sort paths to keep trajectories reproducible.
        for path in sorted(accepted_files)
    ]
    # Return the safe metadata with no limit error.
    return artifacts, None


# Define the Docker-sandboxed Python execution tool.
def run_python(code: str) -> dict:
    """Execute short generated Python code in a restricted Docker container.

    Args:
        code: Complete Python source. Save output files beneath /output.

    Returns:
        A JSON-compatible execution result containing text and artifact metadata.
    """
    # Encode once so the byte limit matches what Docker receives.
    code_bytes = code.encode("utf-8")
    # Reject empty source before starting Docker.
    if not code.strip():
        # Return the documented failure shape.
        return {"ok": False, "status": "execution_error", "error": "Python code is required."}
    # Enforce the source-size limit.
    if len(code_bytes) > MAX_CODE_BYTES:
        # Do not send oversized generated code to Docker.
        return {
            "ok": False,
            "status": "execution_error",
            "error": f"Python code exceeded {MAX_CODE_BYTES} bytes.",
        }
    # Locate the Docker CLI without invoking a shell.
    docker = shutil.which("docker")
    # Report a missing CLI separately from daemon failures.
    if not docker:
        # Provide an actionable structured error.
        return {"ok": False, "status": "docker_unavailable", "error": "Docker is not installed."}
    # Prevent a stalled Docker daemon from raising into the terminal loop.
    try:
        # Check that the daemon is reachable before creating local output state.
        daemon_check = subprocess.run(
            # Ask only for the server version.
            [docker, "info", "--format", "{{.ServerVersion}}"],
            # Capture output for a concise error.
            capture_output=True,
            # Decode output as text.
            text=True,
            # Bound the preflight.
            timeout=5,
            # Handle the result ourselves.
            check=False,
        )
    # Convert local process and timeout failures into tool data.
    except (OSError, subprocess.TimeoutExpired) as error:
        # Return a stable daemon-unavailable status.
        return {"ok": False, "status": "docker_unavailable", "error": str(error)}
    # Stop when Docker Desktop or the daemon is unavailable.
    if daemon_check.returncode != 0 or not daemon_check.stdout.strip():
        # Return the final diagnostic line without exposing environment variables.
        error = daemon_check.stderr.strip().splitlines()
        # Select a compact diagnostic.
        message = error[-1] if error else "Docker daemon is unavailable."
        # Return a stable failure status.
        return {"ok": False, "status": "docker_unavailable", "error": message}
    # Read the configured immutable image tag.
    image = os.getenv("SANDBOX_IMAGE", "agents-python-sandbox:1")
    # Convert image-inspection process errors into a stable tool failure.
    try:
        # Verify the image tag exists locally; tool calls never build or pull images.
        image_check = subprocess.run(
            # Filter image listings by exact reference; Docker Desktop handles this more reliably than inspect-by-tag.
            [docker, "image", "ls", "--quiet", "--filter", f"reference={image}"],
            # Capture the matching image ID.
            stdout=subprocess.PIPE,
            # Capture daemon diagnostics.
            stderr=subprocess.PIPE,
            # Decode diagnostics.
            text=True,
            # Bound the preflight.
            timeout=5,
            # Handle a missing image as structured data.
            check=False,
        )
    # Treat a stalled daemon as unavailable rather than as a missing image.
    except (OSError, subprocess.TimeoutExpired) as error:
        # Return a stable daemon failure.
        return {"ok": False, "status": "docker_unavailable", "error": str(error)}
    # Explain how to build the image when it is absent.
    if image_check.returncode != 0 or not image_check.stdout.strip():
        # Return a stable failure with the configured image name.
        return {
            "ok": False,
            "status": "image_missing",
            "error": f"Sandbox image '{image}' is missing. Run: docker build -t {image} sandbox",
        }
    # Apply age and count retention before creating this run.
    _prune_artifacts()
    # Generate a unique identifier shared by the directory and container.
    run_id = str(uuid4())
    # Create this run's only host-writable directory.
    run_directory = (ARTIFACTS_DIRECTORY / run_id).resolve()
    # Create the empty output mount.
    run_directory.mkdir(parents=True, exist_ok=False)
    # Allow the non-root container user to write through Docker Desktop's bind mount.
    run_directory.chmod(0o777)
    # Give the ephemeral container a collision-resistant name.
    container_name = f"agents-python-{run_id}"
    # Build the complete shell-free Docker command.
    command = [
        docker,
        "run",
        "--rm",
        # Keep container stdin open so generated source reaches `python -`.
        "--interactive",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--user",
        "10001:10001",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--env",
        "MPLBACKEND=Agg",
        "--env",
        "MPLCONFIGDIR=/tmp/matplotlib",
        "--env",
        "OPENBLAS_NUM_THREADS=1",
        "--env",
        "OMP_NUM_THREADS=1",
        "--env",
        "MKL_NUM_THREADS=1",
        "--mount",
        f"type=bind,src={run_directory},dst=/output",
        image,
        "python",
        "-",
    ]
    # Record the monotonic start time for timeout and latency.
    started = time.monotonic()
    # Start the isolated process with binary pipes and no shell.
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Confirm all three configured pipes exist.
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    # Send generated code only through standard input.
    process.stdin.write(code_bytes)
    # Close stdin so Python sees end-of-file and begins execution.
    process.stdin.close()
    # Monitor stdout and stderr without allowing either pipe to block the other.
    selector = selectors.DefaultSelector()
    # Register standard output under its result key.
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    # Register standard error under its result key.
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    # Retain each stream separately for the tool result.
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    # Default to normal completion unless a host-enforced limit changes it.
    forced_status = None
    # Read until both pipes close or a limit is reached.
    while selector.get_map():
        # Calculate remaining wall-clock execution time.
        remaining = TIMEOUT_SECONDS - (time.monotonic() - started)
        # Stop an execution that exhausted its wall-clock budget.
        if remaining <= 0:
            # Record the documented timeout status.
            forced_status = "timeout"
            # Force-remove the named container, not merely the Docker CLI.
            _remove_container(docker, container_name)
            # Stop reading after cleanup.
            break
        # Wait briefly for either output stream.
        ready = selector.select(timeout=min(0.2, remaining))
        # Read each available stream in bounded chunks.
        for key, _ in ready:
            # Read bytes directly from the selected pipe.
            chunk = os.read(key.fileobj.fileno(), 8192)
            # Unregister a stream after end-of-file.
            if not chunk:
                # Stop polling the closed pipe.
                selector.unregister(key.fileobj)
                # Continue with other streams.
                continue
            # Append bytes to the correct capture buffer.
            captured[key.data].extend(chunk)
            # Calculate combined retained output.
            combined_size = len(captured["stdout"]) + len(captured["stderr"])
            # Stop an execution that exceeds the output boundary.
            if combined_size > MAX_OUTPUT_BYTES:
                # Trim the last chunk to keep the result itself bounded.
                overflow = combined_size - MAX_OUTPUT_BYTES
                # Remove overflow bytes from the stream that crossed the limit.
                del captured[key.data][-overflow:]
                # Record the documented output-limit status.
                forced_status = "output_limit"
                # Force-remove the container immediately.
                _remove_container(docker, container_name)
                # Leave the per-stream loop.
                break
        # Leave the outer loop after any forced stop.
        if forced_status:
            # Stop polling both pipes.
            break
    # Close selector resources.
    selector.close()
    # Ensure the Docker CLI process itself is no longer alive.
    try:
        # Wait briefly for --rm cleanup after normal or forced completion.
        exit_code = process.wait(timeout=5)
    # Handle a stuck Docker CLI after container cleanup.
    except subprocess.TimeoutExpired:
        # Kill only the local Docker client process.
        process.kill()
        # Collect its final return code.
        exit_code = process.wait(timeout=5)
    # Measure complete sandbox latency.
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    # Decode retained output safely.
    stdout = bytes(captured["stdout"]).decode("utf-8", errors="replace")
    # Decode retained diagnostics safely.
    stderr = bytes(captured["stderr"]).decode("utf-8", errors="replace")
    # Restore conservative host permissions after container execution.
    run_directory.chmod(0o755)
    # Inspect only allowed output formats.
    artifacts, artifact_error = _collect_artifacts(run_directory)
    # Reject and remove a run that violates artifact boundaries.
    if artifact_error:
        # Remove all artifacts from the violating run.
        shutil.rmtree(run_directory, ignore_errors=True)
        # Return execution evidence with an artifact-limit status.
        return {
            "ok": False,
            "status": "artifact_limit",
            "error": artifact_error,
            "code": code,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "artifacts": [],
        }
    # Remove an empty directory when the program produced no files.
    if not artifacts:
        # Keep stdout-only executions from accumulating empty directories.
        run_directory.rmdir()
    # Re-apply retention after this run so no more than fifty artifact runs remain.
    _prune_artifacts()
    # Return a timeout or output-limit result after collecting any safe partial files.
    if forced_status:
        # Describe the host-enforced stop.
        error = (
            f"Execution exceeded {TIMEOUT_SECONDS} seconds."
            if forced_status == "timeout"
            else f"Combined stdout and stderr exceeded {MAX_OUTPUT_BYTES} bytes."
        )
        # Return structured failure evidence.
        return {
            "ok": False,
            "status": forced_status,
            "error": error,
            "code": code,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "artifacts": artifacts,
        }
    # Convert a nonzero Python exit into a documented execution failure.
    if exit_code != 0:
        # Return diagnostics and any safe artifacts created before failure.
        return {
            "ok": False,
            "status": "execution_error",
            "error": "Generated Python exited with a nonzero status.",
            "code": code,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "artifacts": artifacts,
        }
    # Return the complete successful result.
    return {
        "ok": True,
        "status": "completed",
        "code": code,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "artifacts": artifacts,
    }


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


def list_resume_pdfs() -> dict:
    """List PDF metadata from only the configured resume folder."""
    from .google_workspace import GoogleWorkspace

    files = GoogleWorkspace().list_resume_pdfs()
    return {"ok": True, "count": len(files), "files": files}


def extract_resume_pdf(file_id: str) -> dict:
    """Download and extract one PDF after proving it belongs to the folder."""
    from .google_workspace import GoogleWorkspace

    workspace = GoogleWorkspace()
    metadata = next(
        (item for item in workspace.list_resume_pdfs() if item["id"] == file_id),
        None,
    )
    if metadata is None:
        return {"ok": False, "error": "PDF is not in the configured resume folder."}
    path = None
    try:
        path = workspace.download_resume_pdf(metadata)
        return {"ok": True, **workspace.extract_pdf_text(path)}
    finally:
        if path:
            path.unlink(missing_ok=True)


def read_screening_criteria() -> dict:
    """Return the validated editable Full-Stack AI Engineer rubric."""
    from .google_workspace import GoogleWorkspace
    from .resume_screening import CandidateRegistryAgent

    registry = CandidateRegistryAgent(GoogleWorkspace())
    registry.initialize()
    return {"ok": True, **registry.read_criteria()}


def write_screening_results(rows_json: str) -> dict:
    """Write pre-scored rows using the registry's idempotent file-ID policy."""
    from .google_workspace import GoogleWorkspace
    from .resume_screening import CandidateRegistryAgent, RESULT_HEADERS

    try:
        rows = json.loads(rows_json)
    except json.JSONDecodeError as error:
        return {"ok": False, "error": f"Invalid rows JSON: {error}"}
    if not isinstance(rows, list) or len(rows) > 100:
        return {"ok": False, "error": "rows_json must contain at most 100 rows."}
    allowed_statuses = {"SHORTLISTED", "NOT_SHORTLISTED", "REVIEW_REQUIRED", "ERROR"}
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("drive_file_id"):
            return {"ok": False, "error": "Every row needs drive_file_id."}
        if row.get("status") not in allowed_statuses:
            return {"ok": False, "error": "Every row needs a valid status."}
        normalized.append({key: row.get(key, "") for key in RESULT_HEADERS})
    registry = CandidateRegistryAgent(GoogleWorkspace())
    registry.initialize()
    registry.write_results(normalized, registry.existing_versions())
    return {"ok": True, "written": len(normalized)}


def get_email_sender_identity() -> dict:
    """Return the authorized Gmail sender without exposing OAuth tokens."""
    from .outreach import get_email_sender_identity as identity

    return {"ok": True, **identity()}


def prepare_outreach_email(candidate_json: str) -> dict:
    """Persist one draft; this function has no send capability."""
    from .outreach import CandidateOutreachAgent, get_email_sender_identity

    try:
        candidate = json.loads(candidate_json)
    except json.JSONDecodeError as error:
        return {"ok": False, "error": f"Invalid candidate JSON: {error}"}
    if not isinstance(candidate, dict):
        return {"ok": False, "error": "candidate_json must be an object."}
    agent = CandidateOutreachAgent(
        os.environ["GEMINI_API_KEY"],
        os.getenv("GEMINI_MODEL", "gemma-4-31b-it"),
    )
    draft = agent.prepare(candidate, get_email_sender_identity()["email"])
    return {
        "ok": True,
        "draft_id": draft["draft_id"],
        "draft_hash": draft["draft_hash"],
        "to": draft["to"],
        "subject": draft["subject"],
        "text_body": draft["text_body"],
    }


def send_approved_email(approval_id: str, draft_hash: str, draft_id: str) -> dict:
    """Send only after the persisted approval guard succeeds."""
    from .outreach import send_approved_email as send

    return {"ok": True, **send(approval_id, draft_hash, draft_id)}


# Map model-visible names to the only Python functions it may execute.
TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "web_search": web_search,
    "grep_code": grep_code,
    "run_python": run_python,
    "list_resume_pdfs": list_resume_pdfs,
    "extract_resume_pdf": extract_resume_pdf,
    "read_screening_criteria": read_screening_criteria,
    "write_screening_results": write_screening_results,
    "get_email_sender_identity": get_email_sender_identity,
    "prepare_outreach_email": prepare_outreach_email,
    "send_approved_email": send_approved_email,
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
    # Declare the Docker-sandboxed Python execution tool.
    {
        "name": "run_python",
        "description": "Run short Python for calculations, analysis, or charts in a network-isolated Docker sandbox. Save result files under /output.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Complete minimal Python source. Print textual results and save charts or data beneath /output.",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "list_resume_pdfs",
        "description": "List PDF resumes directly inside the configured Google Drive folder.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "extract_resume_pdf",
        "description": "Download and extract text from one PDF in the configured resume folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Drive file ID returned by list_resume_pdfs.",
                }
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "read_screening_criteria",
        "description": "Read and validate the Full-Stack AI Engineer screening rubric.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "write_screening_results",
        "description": "Write validated pre-scored candidate records to Google Sheets.",
        "parameters": {
            "type": "object",
            "properties": {
                "rows_json": {
                    "type": "string",
                    "description": "JSON array of candidate result objects.",
                }
            },
            "required": ["rows_json"],
        },
    },
    {
        "name": "get_email_sender_identity",
        "description": "Return the authorized Gmail sender address without token data.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "prepare_outreach_email",
        "description": "Prepare and persist an outreach draft without sending it.",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_json": {
                    "type": "string",
                    "description": "Validated shortlisted candidate fields as JSON.",
                }
            },
            "required": ["candidate_json"],
        },
    },
    {
        "name": "send_approved_email",
        "description": "Send a persisted draft only when approval ID and draft hash match.",
        "parameters": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "draft_hash": {"type": "string"},
                "draft_id": {"type": "string"},
            },
            "required": ["approval_id", "draft_hash", "draft_id"],
        },
    },
]
