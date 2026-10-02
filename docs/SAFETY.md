# Safety model

What the orchestrator does and does not protect, and where each protection lives. Vulnerability
reporting is in [SECURITY.md](../SECURITY.md); the tools themselves are listed in
[TOOLS.md](TOOLS.md).

This is a **project boundary, not a virtual machine**. Package scripts, test runners, and
generated programs are ordinary code running as you. Run the whole orchestrator inside a
container or VM when the request or its dependencies are untrusted.

## Filesystem boundary

Agents read and write files only through the workspace tools, which:

- accept only relative paths and reject `..`, absolute paths, and symlink escapes;
- reject `.git` and the controller-owned `.engineering-team/` directory **after** symlink
  resolution and case-insensitively, so aliases cannot reach them (exceptions: the agent
  scratch directory `.engineering-team/tmp/`, and read-only access to command logs under
  `.engineering-team/runs/<run-id>/commands/`);
- limit text read and write sizes, check exact-replacement counts, and protect the workspace
  root from deletion;
- apply an optional per-agent **write scope** (gitignore-style globs, deny wins) to every
  write, replace, delete, patch, move, copy, and mkdir, so parallel agents own disjoint paths.

A project directory is *owned* when it contains `.engineering-team/owner.json`. Non-empty
directories without it are never written to, and `--reset` deletes only owned projects
(`--force-reset` overrides that for foreign directories, never for the home directory, the
current directory and its parents, the filesystem root, symlinks, or the installation itself).
`--reset` also refuses a workspace another run is using. See
[ARCHITECTURE.md](ARCHITECTURE.md#run-context-tools-and-locking) for the workspace lock.

## Command execution

Every command passes validation in `tools/commands.py` and then runs through the
`ExecutionBackend`. Validation:

- an executable **allowlist** (common runtimes, package managers, test runners, build tools);
  add one narrowly with `ENGINEERING_COMMAND_ALLOWLIST`;
- **no shell**: pipes, redirection, chaining, and inline code flags (`python -c`, `node -e`)
  are rejected, as are absolute path arguments that leave the workspace or name protected
  storage;
- the working directory must be inside the workspace.

The child process gets a **scrubbed environment**: a short safe list (`PATH`, locale, proxy and
certificate variables), plus any names in `ENGINEERING_SUBPROCESS_ENV_ALLOWLIST`; API keys are
never inherited. `HOME`, `TMPDIR`, and package caches point inside `.engineering-team/`, and
`CI=1` and `NO_COLOR=1` make tools non-interactive.

### Local backend (default)

`LocalBackend` starts each command in **its own session and process group** with stdin closed,
streams stdout and stderr to a capped log (`.engineering-team/runs/<run-id>/commands/<n>.log`,
5 MB) and keeps only a head-and-tail window in memory, so an output flood cannot exhaust memory.
On timeout or run cancellation it sends `SIGTERM` to the **whole process group** and `SIGKILL`
after three seconds, which also removes grandchildren a script spawned. Descendants still
holding the output pipe when the command exits are killed too. Windows is not supported.

Limits worth knowing: the local backend cannot enforce `CommandSpec.network=False`, restrict
what a process reads outside the workspace, or cap CPU and memory. A process that deliberately
leaves its process group (`setsid`) can outlive a timeout. The Docker backend (planned) is the
answer to all of these.

### Docker backend

Not implemented yet; this section will describe the container sandbox, its mounts, network
policy, and resource limits when it lands.

## Network

Tools that need the network are off unless enabled in settings. Optional documentation MCP
servers are disabled by default; configure only servers you trust, since they add network
access and their own data-handling boundary.

## Untrusted content

Text from the repository, command output, and the web is data. Controller code never treats
agent-authored text as evidence, and tool output can never change permissions or instructions.
