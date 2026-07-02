# Security notes

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
