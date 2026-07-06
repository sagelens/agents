# Security notes

Phoenix is local, unauthenticated, and intended only for localhost development.
It stores bounded prompts, answers, delegation data, and tool inputs/outputs in
a persistent Docker volume. API keys and environment contents are never span
attributes. Do not expose port 6006 publicly.

## API keys

An API key is a bearer secret: anyone holding it may use its quota and incur
cost. The key pasted into chat should be revoked immediately. Store its
replacement only in `.env`, never source code, screenshots, logs, or trajectory
arguments.

## Model boundary

Model output is untrusted. This project:

- permits only names in `TOOL_FUNCTIONS`;
- passes arguments as normal Python keyword arguments;
- never calls `eval` or a shell;
- limits the number of model calls;
- limits web-search result count;
- applies HTTP timeouts; and
- returns failures as observations instead of hiding them.

The coordinator cannot call application tools. It can only request one of
three specialists. Each specialist receives a fresh client, a focused task,
brief context, and the minimum required tool allowlist. Specialists cannot
delegate to each other, access coordinator history, or retain private memory.

## Prompt injection

A search result can contain text telling the agent to ignore instructions or
reveal secrets. The system prompt labels tool output as untrusted data. Stronger
production defenses include isolating retrieved content, URL allow/deny lists,
content-size limits, output validation, and human approval before side effects.

These tools are read-only, which keeps the first version's risk substantially
smaller than tools that send email, modify files, or purchase products.

## Repository search boundary

`grep_code` resolves every requested path and verifies it remains beneath
`CODEBASE_ROOT`. It invokes ripgrep with an argument list and `shell=False`, so
patterns cannot become shell commands. It disables symlink following and
excludes `.env`, key material, databases, dependency folders, Git metadata,
virtual environments, and generated trajectory data.

Search results are still untrusted. Source comments and strings may contain
prompt-injection instructions, so the system prompt explicitly treats them as
data. The runtime boundary remains the primary defense: the grep tool is
read-only and cannot broaden its own allowed root.

## Generated Python sandbox

Generated code runs in a new non-root container with no network, a read-only
root filesystem, all Linux capabilities dropped, no-new-privileges enabled,
and explicit CPU, memory, process, time, output, and artifact limits. The
container receives no API keys, environment secrets, repository mount, Docker
socket, or host home directory.

Only one empty run-specific directory is mounted at `/output`. Symlinks and
unsupported output formats are removed, and allowed artifacts are capped at
five files and five megabytes total. Source is sent through stdin using a
shell-free argument list.

The Docker daemon remains a privileged host service, and containers do not
provide an absolute security boundary. Do not expose this local design as a
public multi-tenant execution endpoint without stronger isolation and
operational controls.

## Resume screening data

- `keys.json` is a service-account secret and must remain ignored by Git.
- Drive access is read-only; Sheets write access is limited by the service
  account's sharing permissions and the configured spreadsheet ID.
- File IDs must belong to the configured folder before download.
- Temporary PDFs are bounded and deleted after processing.
- Protected and irrelevant personal attributes are excluded from scoring.
- Raw resume text is not stored in traces or printed to the terminal.
- Automated ranking requires appropriate human governance for hiring use.

## Email outreach

- OAuth requests `gmail.send` and identity only; it cannot read the mailbox.
- OAuth tokens and full drafts are ignored by Git and stored owner-only.
- The draft-generation agent has no sending authority.
- Every email requires a persisted per-draft approval ID and SHA-256 hash.
- Edits invalidate approval and require another human decision.
- Gmail calls are not automatically retried because delivery can be ambiguous.
- `SENDING`, `SENT`, and `UNKNOWN` block automatic duplicate sends.
- Full email bodies never enter metadata audit logs.

## Resume prompt injection

Resumes pass through PDF-span inspection, normalization, deterministic
signals, an isolated tool-free guard, evidence provenance, and evidence-only
scoring. Suspicious or failed analysis stops at manual review and cannot
shortlist or outreach.

See [PROMPT_INJECTION_SECURITY.md](PROMPT_INJECTION_SECURITY.md).
