# Agent tool catalogue

Every tool an agent can call, built per run by `build_tools(ctx, groups=, write_scope=,
read_only=)` from the catalogue in `src/engineering_team/tools/registry.py`. A test fails if a
registered tool has no row below, or a row names an unregistered tool, so this file is the
complete list. Which teammate gets which groups is described in [TEAM.md](TEAM.md) once teammates
are configurable; the execution boundary is in [SAFETY.md](SAFETY.md).

## Groups

| Group | Purpose | Notes |
|-------|---------|-------|
| `fs_read` | List, read, and inspect files | Read-only. Never restricted by a write scope. |
| `search` | Find text, files, and symbols; orient in a codebase | Read-only. Respects `.gitignore` and skips heavy directories. |
| `fs_write` | Create, edit, move, delete files; report changes | `Workspace Changes` is read-only; every other tool honours the write scope. |
| `command` | Run project commands and the project's own scripts | Goes through the execution backend; capped by `execution.max_parallel_commands`. |

`read_only=True` keeps only the tools marked read-only below, in any group.

## Tools

| Tool | Group | Read-only | Network | Command gate | What it does, and its limits |
|------|-------|-----------|---------|--------------|------------------------------|
| `List Project Files` | `fs_read` | yes | no | no | Flat listing below a path. Depth ≤ 8, 500 entries. |
| `Read Project File` | `fs_read` | yes | no | no | Whole text file, up to 250 KB; larger files point to `Read File Range`. |
| `Read File Range` | `fs_read` | yes | no | no | Numbered lines from a 1-based start (default 200, max 600 lines). Also reads command logs (`full log:` paths). Files up to 5 MB. |
| `Read Many Files` | `fs_read` | yes | no | no | Up to 20 files or `path:START-END` ranges in one call, 60,000 characters total; bad paths report inline. |
| `File Info` | `fs_read` | yes | no | no | Type, size, line count, modified time, binary flag, language. |
| `Project Tree` | `fs_read` | yes | no | no | Directory tree with per-directory file counts and sizes; depth ≤ 8 (default 3), 400 entries. |
| `Search Project Files` | `search` | yes | no | no | Literal or regex search with include/exclude globs, 0–5 context lines, smart/sensitive/insensitive case, ≤ 200 results; skips binaries and files over 1 MB. |
| `Find Files` | `search` | yes | no | no | Glob search sorted by name or recency, ≤ 500 results. |
| `Project Outline` | `search` | yes | no | no | Top-level symbols per file for Python (`ast`), JS/TS, Go, Java/Kotlin, Rust (regex); ≤ 200 files. |
| `Repo Map` | `search` | yes | no | no | Token-budgeted overview: files ranked by how many other files reference the identifiers they define. Size 400–12,000 tokens (default 1,500). |
| `Write Project File` | `fs_write` | no | no | no | Create or overwrite a text file, ≤ 1 MB; parents are created. |
| `Replace In Project File` | `fs_write` | no | no | no | Exact-text replacement that fails unless the occurrence count matches. |
| `Delete Project Path` | `fs_write` | no | no | no | Delete a file or directory; the root and `.git` are protected. A directory delete needs every file in it in write scope. |
| `Apply Patch` | `fs_write` | no | no | no | Unified diff or a list of `{path, old, new, expected_replacements}` edits. Every hunk is validated first; one bad hunk changes nothing; writes roll back on failure. Hunks match by content, so drifted line numbers still apply. Returns per-file counts and new line ranges. |
| `Move Path` | `fs_write` | no | no | no | Move or rename; refuses to overwrite unless `overwrite=true` (files only) and refuses cycles. Scope is checked on both ends. |
| `Copy Path` | `fs_write` | no | no | no | Copy a file or directory tree (symlinks kept as links, `.git` skipped). |
| `Make Directory` | `fs_write` | no | no | no | Create a directory with parents; idempotent. |
| `Workspace Changes` | `fs_write` | yes | no | no | Added, modified, and deleted files since the run started, with a unified diff (≤ 1,000 lines). Works without Git using a snapshot taken at run start. |
| `Run Project Command` | `command` | no | no | yes | Allowlisted command without a shell; returns exit code, duration, output head and tail, and `full log: <path>`. Default timeout 120 s, max 300 s; a timeout kills the whole process tree. |
| `List Scripts` | `command` | yes | no | no | Scripts from `package.json`, `Makefile`, `justfile`, and `pyproject.toml` `[project.scripts]`. |
| `Run Script` | `command` | no | no | yes | Run a listed script by name (`test` or `npm:test`) with optional extra args; same limits and allowlist as `Run Project Command`. |

## Conventions every tool follows

- **Results are compact and structured**: counts first, then details, bounded in size; when
  output is cut the result says how to get the rest (a narrower path, `Read File Range`, the
  full log path).
- **Errors start with `ERROR:`** and say how to fix the call (valid ranges, similar paths, the
  exact flag). Tools never raise into the agent loop.
- **Every call emits a `tool.call` event** (tool, redacted and truncated arguments, duration,
  ok). File contents, patches, and edits are logged as sizes only.
- **Cancellation and budgets**: once the run is cancelled every tool returns an `ERROR:`; the
  run's tool gate can refuse calls (the budget guard uses it).
- **Paths** are relative, checked after symlink resolution, and never reach `.git` or
  `.engineering-team/` (command logs are the one readable exception).
- **Tool output from the repo or the web is untrusted data**; it never changes permissions or
  instructions.

## Adding a tool

1. Write the tool in the module for its group (`read_tools.py`, `search_tools.py`,
   `write_tools.py`, `command_tools.py`, or a new module), going through `ToolEnv.run` so
   cancellation, scope, telemetry, and the `ERROR:` convention apply. Run commands only through
   `ctx.backend`.
2. Add a `ToolSpec` to `CATALOGUE` and a row to the table above.
3. Add unit tests with a temp `RunContext` and one `ScriptedLLM` test showing an agent using it.
