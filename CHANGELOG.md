# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every implementation task in [docs/IMPLEMENTATION_PLAN_0.2.0.md](docs/IMPLEMENTATION_PLAN_0.2.0.md)
adds its user-visible changes to the `Unreleased` section below. The final
release task renames that section to `0.2.0` and dates it.

## [Unreleased] — 0.2.0

Work in progress. Planned scope is tracked in
[docs/IMPLEMENTATION_PLAN_0.2.0.md](docs/IMPLEMENTATION_PLAN_0.2.0.md); entries are added
as each task lands.

### Added

- Layered, typed configuration (`engineering-team.toml`, user config, `ENGINEERING_*` environment, CLI) with
  per-value provenance; `engineering-team config show` prints every setting and where it came from, with secrets
  masked. See `docs/CONFIGURATION.md`.
- Price-ordered provider presets (`--provider`): OpenAI `gpt-6.1-sol` + `gpt-6-luna`, Anthropic `claude-sonnet-5-5` +
  `claude-opus-5-5`, Google `gemini-3.8-flash`, and local Ollama `qwen3.8:27b`; tiers (`max`, `lead`, `reviewer`,
  `worker`, `cheap`), a `max-quality` profile, per-tier/profile/role model overrides, and model prices in `config show`.
  Azure is disabled by default (`enable_azure = true`). Anthropic, Google, and Azure SDKs are optional extras
  (`uv sync --extra anthropic`).
- Missing credentials or provider SDKs are reported up front in one line instead of failing mid-run.
- GitHub Actions CI (Python 3.11–3.13 on Linux, 3.12 on macOS): lint, format check, mypy,
  tests, and a clean-venv wheel smoke test; Dependabot for `uv` and Actions.
- `LICENSE` (MIT) and package metadata (authors, classifiers, keywords, project URLs).
- Tool catalogue (`docs/TOOLS.md`, `tools/registry.py`) with groups, and a much richer toolbelt for agents:
  Search Project Files, Find Files, Read File Range, Read Many Files, File Info, Project Tree, Project
  Outline, Repo Map (a token-budgeted, reference-ranked overview), Apply Patch (atomic unified diff or edit
  list), Move/Copy Path, Make Directory, Workspace Changes (a diff of the run's edits, with or without Git),
  List Scripts and Run Script (package.json, Makefile, justfile, pyproject scripts).
- Every tool call emits a redacted `tool.call` event and honours cancellation and a tool-call gate.
- Execution backend abstraction (`ExecutionBackend`, local implementation) that streams command output to a
  capped log under `.engineering-team/runs/<run-id>/commands/`; `docs/SAFETY.md` describes the boundary.
- Offline FakeLLM test utilities (`engineering_team.testing`: `ScriptedLLM`, agent/task helpers) and `live`/`docker`
  pytest markers that skip by default; tests now fail if they try to reach a non-loopback host.
- Run records: every run gets a sortable run ID and a `runs/<run-id>/` directory with `manifest.json` (status,
  request/settings hashes, versions, stages) and an `events.jsonl` log of tool calls, model calls with token
  usage, tasks, and agents; secrets are scrubbed from the log. See `docs/ARCHITECTURE.md`.
- Usage and cost reporting: every run prints exact token counts and an estimated cost (or `unknown` when a model
  has no price, never `$0`) and records `usage.json` by stage, agent, and model. Prices ship in
  `data/pricing.toml` with their source and the date they were checked; override them with a `[pricing]` table.
- Run budgets: `budget.max_cost_usd`, `max_tokens`, `max_wall_seconds`, and `max_tool_calls` now stop an
  overspending run (warning at 80%). See `docs/CONFIGURATION.md` for how in-flight overrun is handled.
- Typed contracts (`engineering_team.contracts`) for specs, plans, checks, findings, manifests, and events.
- Workspace lock: one run per workspace. A second run (or `--reset`) against a workspace that is in use stops
  with a clear message naming the holder; the lock is released automatically if the holder crashes.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull-request templates.

### Changed

- `config show` takes model prices from the price table (and marks models without one as `price unknown`).

- The request and run metadata moved from `.engineering-team/request.md` and `run.json` into each run's directory
  (`request.md`, `settings.json`, `manifest.json`).

- Run Project Command now returns the exit code with the duration, the combined stdout/stderr (head and tail
  when long), and a `full log:` path that Read File Range can page through. Read-only mode now keeps every
  read-only tool (search, outline, repo map, changes) rather than just list/read.

- Default OpenAI models are now `gpt-6.1-sol` (lead) and `gpt-6-luna` (specialists), called through the Responses
  API with an explicit context window; the superseded `gpt-5.6-*` models are no longer presets. The 0.1.0
  `ENGINEERING_*` model variables keep working (standard names configure the standard profile, `ENGINEERING_SMOKE_*`
  the smoke profile).
- Settings are centralised: only `settings.py` reads configuration, the profile no longer travels through
  `os.environ`, and `EngineeringTeam` takes a `Settings` object.
- Request precedence is now `--request` > `--example` > `--request-file` > `ENGINEERING_PROJECT_REQUEST` >
  `ENGINEERING_REQUEST_FILE` > `PROJECT_REQUEST.md` in the current directory; blank environment values
  count as unset and a blank `--request` is an error.
- The default workspace root is `./workspace` relative to where you run the command (it used to be next to
  the installed package), and relative `ENGINEERING_WORKSPACE_ROOT` values resolve against the current
  directory.
- The bundled Tiny Notes request moved into the package and is used with `--example tiny-notes`; the
  repository-root `PROJECT_REQUEST.md` was removed.
- All entry points return proper exit codes: `0` success, `2` usage/configuration error (one line, no
  traceback), `1` runtime failure, `130` interrupted.
- Artifact guardrails now also reject near-empty files (an interim check until independent verification).
- Upgraded CrewAI to 1.15.23 (supported range `>=1.15.23,<1.16`) and refreshed the lockfile
  and dev tools (pytest 9.1, Ruff 0.16).
- Internal: tools are built per run from a `RunContext` instead of module globals, so concurrent runs and
  parallel agents no longer share a workspace; each run's CrewAI log is now
  `.engineering-team/runs/<run-id>/crew-log.json`, and `EngineeringTeam` takes a `RunContext`.
- The test suite is hermetic: it ignores the developer's `.env`, uses throw-away framework
  storage and a dummy API key, and runs from a temporary directory.

### Fixed

- Commands that time out now have their whole process tree killed (SIGTERM, then SIGKILL), so grandchildren
  no longer survive; command output is streamed to a capped log instead of being buffered without limit.

- An inline `ENGINEERING_PROJECT_REQUEST` no longer overrides an explicit `--request-file`.
- Installed copies no longer look for a request file or create workspaces next to `site-packages`.
- `engineering-team` no longer exits with status 1 after a successful run (the crew result was being
  passed to `sys.exit`).

### Security

- File tools check protected locations after resolving symlinks, so an alias such as `link -> .git`
  can no longer read or modify Git metadata; the orchestrator's `.engineering-team/` state is protected
  the same way.
- `--reset` only deletes projects created by this tool (ownership marker) and always refuses your home
  directory, the current directory and its parents, the filesystem root, symlinks, and the
  engineering-team installation; `--force-reset` is required for foreign directories.
- Existing non-empty directories that this tool did not create are no longer written into.

## [0.1.0] — 2026-07-30

First usable version: a hierarchical CrewAI crew that turns a product request
into a tested MVP inside a persistent, normal project directory.

### Added

- **Hierarchical crew** (`EngineeringTeam`): a tool-free `engineering_lead`
  manager delegates six tasks to four stack-agnostic specialists
  (`solution_architect`, `backend_engineer`, `frontend_engineer`,
  `quality_engineer`).
- **Six-stage delivery pipeline**: architecture → foundation → backend/core →
  frontend/experience → quality verification → release review, defined in
  `config/agents.yaml` and `config/tasks.yaml`.
- **Product request input** from a Markdown file (`--request-file`, default
  `PROJECT_REQUEST.md`), an inline argument (`--request`), or the
  `ENGINEERING_PROJECT_REQUEST` / `ENGINEERING_REQUEST_FILE` environment variables.
  Template placeholder requests are rejected.
- **Persistent project workspaces** under `workspace/<project-name>/`; reruns
  resume the same directory, `--reset` starts over, `--prepare-only` validates
  inputs without calling an LLM.
- **Project-scoped agent tools**: list, read, write, exact-replace, delete, and
  run-command, with traversal/symlink-escape protection, size limits, a command
  allowlist (`ENGINEERING_COMMAND_ALLOWLIST`), no shell, blocked inline-code
  flags, per-project home/cache directories, and a secret-stripped child
  environment (`ENGINEERING_SUBPROCESS_ENV_ALLOWLIST`). Mutating tools are never
  cached.
- **Model tiers and run profiles**: flagship lead plus lower-cost workers, and a
  `smoke` profile (auto-selected by a marker in the request, or via `--profile`)
  for cheap end-to-end checks. Models, reasoning effort, and iteration caps are
  configurable through `ENGINEERING_*` variables.
- **Artifact guardrails** requiring `docs/architecture.md`,
  `docs/implementation-plan.md`, `README.md`, `docs/verification.md`, and
  `docs/release-report.md` to exist before the related task passes.
- **Opt-in extras**: CrewAI tracing and remote documentation MCP servers
  (`ENGINEERING_DOCS_MCP_URLS`).
- **Entry points**: `engineering-team`, `run_crew`, `train`, `replay`, `test`,
  `run_with_trigger`.
- **Bundled example**: a low-cost "Tiny Notes CLI" smoke request.
- **Tests and tooling**: pytest suite (20 tests, no LLM calls), Ruff, `uv`
  lockfile, `AGENTS.md` CrewAI reference for coding assistants.

### Known limitations

- Task guardrails only check that files exist; they do not prove the code works.
- One global active workspace and one fixed linear pipeline, so no concurrent runs
  or parallel work.
- Always starts a fresh run; no checkpoint/resume, cost reporting, or budgets.
- Commands run on the host; this is a project boundary, not a sandbox.
- Greenfield only: no mode for existing repositories, features, or bug fixes.
- CLI only, no run history or reports.

[Unreleased]: https://github.com/alexandre0sheva/crewai-engineering_team/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alexandre0sheva/crewai-engineering_team/releases/tag/v0.1.0
