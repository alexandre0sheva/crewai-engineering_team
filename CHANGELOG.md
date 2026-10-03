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

- `--strategy pipeline` (or `ENGINEERING_STRATEGY`): a staged run (spec, plan, foundation, implement, verify,
  release) driven by a recipe (`modes/recipes/new.yaml`) as a CrewAI Flow with typed hand-offs: one small crew per
  stage, work packages chosen by the architect's plan (a plan without any skips `implement`), the task board kept
  by the controller (a card per stage and per work package), and the controller, not the agent, checking that
  promised files exist. Per-stage state is written to `pipeline.json` in the run directory.
- `engineering-team resume <run_id>` (and `resume()` in Python): continue a cancelled, interrupted, or failed
  pipeline run without redoing finished stages. A stage counts as finished only while the workspace still matches
  the tree hash recorded when it ended; anything else runs again with an instruction to inspect the workspace
  first. A changed request starts a new run instead of reusing old results.
- `engineering-team cancel <run_id>` (from another terminal), and Ctrl-C / SIGTERM now stop a run at its next safe
  point and end it `cancelled` (exit code 130); a second Ctrl-C stops immediately.
- Parallel work packages in the `pipeline` strategy: packages that do not depend on each other and own disjoint
  paths run at the same time, up to `parallel.max_parallel_agents` (default 3; `1` is strictly sequential). Each
  package's agent can write only the paths it owns (never the shared README, manifests, and lockfiles), packages that
  might overlap are run in turn, a failed package is retried once and does not stop its siblings (its dependents are
  skipped), and a new sequential `integrate` stage builds, tests, and fixes where the packages meet. Plans are
  validated first (ownership, criteria, cycles) and a bad plan goes back to the architect once.
- Events, board cards, browser sessions, and processes are tagged per parallel lane; `parallel.max_rpm` now caps model
  calls per minute across all agents of a run. `run_parallel_readonly` runs reviewers and analysts side by side with
  read-only tools and one report file each.
- `--strategy single`: one agent with every tool and one task, the baseline the other strategies are measured against.
- Git-backed history: a new project becomes a Git repository with an initial commit, and the `pipeline` and `single`
  strategies commit after every finished stage (`stage(plan): ...`), plus a final commit for anything the last check
  changed, for free rollback and a readable log. `--no-git` (or `git.enabled = false`) turns it off; `git.author_name` and
  `git.author_email` set the author. The controller never pushes, fetches, runs hooks, or touches a repository that is not the
  project's own. `GitPort` can also export the work as a patch (`git apply`-able, binary files included).
- Read-only Git tools (new group `git_read`, given to every pipeline teammate): `Git Info` (status, diff, log, show, blame),
  `Git History Search` (who added or removed some text; a file's history), and `Git Diff Between Refs`.
- Independent verification in the `pipeline` strategy: the `verify` stage is now run by the controller, which executes the
  project's checks itself (your `--checks FILE`, then the commands the plan declares, then detected defaults: tests,
  lint, type check, build, and a startup smoke check), records each result with its exit code, log, and the workspace
  revision it ran against, and writes `docs/verification.md` from those results. A missing runtime is `unavailable`,
  never a pass; an empty or untestable project cannot be `verified`; acceptance criteria no passing mapped check proves are
  listed as manual/unverified; each check is a board card the controller moves.
- Bounded repair loop: while a required check fails, the controller hands the structured failures to the quality
  engineer, then runs the checks again (`budget.max_repair_rounds`, default 3, shared by the run). Only the
  controller's own re-run counts; an agent's claim does not. An edit after the last verification (for example by the
  release stage) triggers a final re-verify.
- New exit codes: `3` when the controller's checks failed and `4` when verification was partial (a required check could not
  run); the manifest records the `verdict` (`verified`, `failed`, `partial`). Both are resumable.
- `--checks FILE` (and `[verify]` settings): your own checks, with the acceptance criteria they prove and optional
  Playwright `browser_script` checks. The file must live outside the project and is pinned at the start of the run;
  a copy that changes mid-run stops verification.
- Optional headless-browser tools (`uv sync --extra browser`, then `playwright install chromium` or
  `browser.channel = "chrome"`): `Browser Open`, `Browser Snapshot` (accessibility tree with element refs),
  `Browser Screenshot`, `Browser Click`, `Browser Type`, `Browser Select`, `Browser Press Key`,
  `Browser Wait For`, `Browser Console & Errors`, `Set Viewport`, `Accessibility Check`, and `Browser Close`.
  The browser may open only this run's own localhost ports (every request, redirect hop, and WebSocket is
  checked by a filtering proxy), gets an incognito context per teammate, and is closed with its stage or the
  run. New `[browser]` settings and a `browser` test marker.
- Opt-in web tools (`--allow-web`, `ENGINEERING_ALLOW_WEB`, or `[web] enabled`): `Web Search` (Serper, Brave, or
  Tavily), `Fetch URL` (readable Markdown, SSRF-safe: private, loopback, and metadata addresses are refused after
  DNS resolution and on every redirect), and `Package Info` (PyPI, npm, crates.io, Go: latest version, license,
  deprecation). Off by default and not registered at all unless enabled; per-role allow, request cap, domain
  lists, every request logged as a `web.request` event, all results labelled as untrusted external content.
- `Search Docs`: offline BM25 search over project docs, `knowledge.context_dirs`, and pages fetched this run.
- Code intelligence tools (group `code_intel`, read-only): `Find Symbol`, `Show Symbol`, `Find References`
  (definition/import/call/other), `Who Imports` (reverse and transitive dependencies), `Imports Of`,
  `Find Related Tests`, `Find TODOs` (author and age from Git blame), `Hotspots` (Git churn x complexity),
  and `Inspect Dependencies` (manifests and lockfiles) for Python, JS/TS, Go, Java/Kotlin, C#, Rust, Ruby,
  and PHP. See `docs/TOOLS.md`.
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
- Task board: a controller-enforced kanban of the run (`board.json` and `board.md` in the run directory) where
  agents cannot mark their own work done, with weighted progress, a WIP limit, and a history of every move. Agents
  get board tools (list, get, add subtask, move, block, unblock, comment, report progress), shared notes, a
  project decision log, and Ask Human (which tells them to proceed on an assumption when nobody can answer).
  Comments from the human reach the assignee's next prompt, and a run can be paused. See `docs/ARCHITECTURE.md`.
- Structured developer tools (`dev` group): Run Tests, Rerun Failed Tests, Run Single Test, Run Linter, Type Check,
  Format Code, Build Project, Coverage Report, Install Dependencies, and Dependency Audit. They detect the stack
  (Python, JavaScript/TypeScript, Go, Rust, Java, C#, Ruby, PHP), run the right tool, and return a compact result: a
  failing test run is a short list of failing tests with file, line, and message rather than a long log. A missing tool is
  reported as `UNAVAILABLE` with an install hint, never as a pass. Timeouts, result caps, and extra allowed executables
  are settings under `[tools.dev]`. See `docs/TOOLS.md` and `docs/CONFIGURATION.md`.
- Runtime tools (`runtime` group): Start Background Process (wait for a port, URL, log line, or delay), List
  Processes, Read Process Logs, Stop Process, Wait For Service, HTTP Request, Check Port, Find Free Port,
  Environment Info, Query SQLite, and Inspect Database Schema. An agent can start a dev server, check an endpoint's
  response, and leave nothing running: background processes are killed when their stage ends, the run is cancelled,
  their lifetime passes, or the run ends or crashes. HTTP requests may only reach this run's own loopback ports
  unless `network.http_allowlist` names a host. Settings: `[runtime]` and `[network]`. See `docs/TOOLS.md`.
- Typed contracts (`engineering_team.contracts`) for specs, plans, checks, findings, manifests, and events.
- Workspace lock: one run per workspace. A second run (or `--reset`) against a workspace that is in use stops
  with a clear message naming the holder; the lock is released automatically if the holder crashes.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull-request templates.
- Docker sandbox (`--sandbox docker`, or `execution.backend = "docker"`): every project command, developer tool,
  verification check, background process, and the controller's Git runs in its own throwaway container (your user,
  no capabilities, read-only system, process/memory/CPU limits, only the project mounted, no network except for
  install commands) and the container is removed on exit, timeout, cancel, and run end. An image is chosen per
  language and configurable under `[execution.docker]`. With Docker missing or stopped a `docker` run refuses to
  start; it never falls back to running on your machine. `local` stays the default. See `docs/SAFETY.md`.
- New command-line interface (Typer and Rich): `new`, `resume`, `status`, `runs`, `board`, `cancel`, `note`, `pause`, `unpause`, `config show`, `doctor`, `init`, `examples`, `team`, with `--json`, `--quiet`, `-v`, `--no-color`, and `--workspace-root`. A run shows a live view (progress, kanban board, parallel lanes, activity, cost against budget) and ends with a summary; `note`, `pause`, and `unpause` steer a run from another terminal; runs are found by id across projects. See `docs/USAGE.md`.

- Requirements intake: `new` takes `--request`, repeatable `--request-file` (merged in order under headers), and stdin
  (`-`); `--context-dir DIR` copies reference documents read-only into the project (with an index) for the team to
  search; line endings are normalised, a request over `intake.max_request_chars` or an unedited template is refused,
  and the same text hashes the same from a file, stdin, or the Python API (`RequestBundle.from_sources`).
  `init --mode new|feature|fix|maintain` writes a request template for the kind of work. Settings: `[intake]`.
- Product Analyst teammate and spec stage: a read-only analyst turns the request into a `Spec` with stable criterion ids
  (`AC-1`, `AC-2`, ...), assumptions, non-goals, open questions, and its confidence; the controller checks the ids
  (one repair attempt) and writes `docs/spec.md`, and the architect gets the spec as its contract.
- Clarifying questions: when the analyst is unsure, `--interactive` asks you at the terminal (at most five, Enter lets
  the team assume) and folds your answers into the spec; otherwise, and whenever stdin is not a terminal, the run
  records the open questions as assumptions and continues.

- Team registry and custom teammates: every teammate has a tier, tool groups, an iteration limit, and an enabled flag; change a
  built-in or add a new teammate (a prompt and some tool groups) in `engineering-team.toml` (`[team.<key>]`) or
  `.engineering-team/team.yaml`, with no Python. Definitions are validated before a run starts. The planner is told who is available,
  work packages can go to custom teammates, and a disabled or missing teammate is replaced by the nearest enabled generalist
  (`team.fallback` event). `engineering-team team list` and `team show KEY` show the roster; `doctor` checks it. See `docs/TEAM.md`.

- Five more teammates: a code reviewer and a security engineer (read-only; the security engineer can run the dependency
  audit), a DevOps engineer, a technical writer, and a debugger (now the repair agent of the verify stage).
- Parallel review: after verification the `new` recipe runs the reviewers side by side in their own lanes; the
  controller validates and merges their findings (duplicates become one finding naming both reviewers) into
  `docs/review.md`; findings at or above `review.fail_on` (default `high`) go to the debugger for one repair round and the
  project is verified again. Optional DevOps (`docs/devops.md`) and documentation (`docs/usage.md`) stages follow; the final
  re-verify covers what they change. `--profile smoke`, `team_profile = "minimal"` (`ENGINEERING_TEAM_PROFILE`), or disabling
  a stage's teammates skips them.

### Changed

- Each pipeline teammate's tools now come from its roster entry (the product analyst is read-only; the browser tools go to the
  frontend, quality, and generalist teammates) instead of one shared list; the `hierarchical` strategy reads prompts, tiers, and
  iteration limits from the roster too.
- The 0.1.0 invocation (`engineering-team --request-file FILE`) now runs `new` and prints a deprecation notice (removed in 0.3.0); CrewAI's console output is off unless you pass `-v`; `replay` takes `--run RUN_ID` or `--project-name` instead of guessing the project from the environment.
- `Hotspots` and `Find TODOs` now read history through `GitPort`: a project inside some other repository no longer reads
  that repository's history, only a repository of its own.
- The pipeline's `verify` stage no longer relies on an agent writing a verification record: `docs/verification.md` is
  rendered by the controller from checks it ran, and what the quality engineer says goes to `docs/qa-notes.md`. The
  size-based artifact guardrail still applies to the other promised files and to the `hierarchical` strategy.
- The `new` recipe gained an `integrate` stage after `implement`. A parallel stage's `retry` now applies per work
  package, and `WorkPackage` has an optional `required` flag.
- New `strategy` setting (`--strategy`, `ENGINEERING_STRATEGY`, `strategy` in the config file): `hierarchical` (the default,
  unchanged), `pipeline`, or `single`. The 0.1.0 invocation keeps working and still uses the hierarchical crew.
- A run that ends `failed`, `cancelled`, or `interrupted` can now be reopened by `resume`; `succeeded` stays final. Stage
  records in the manifest carry the workspace revision at their start and end.
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
- Request sources: explicit `--request` / `--request-file` (merged in that order) or `--example` (on its own) beat
  `ENGINEERING_PROJECT_REQUEST` > `ENGINEERING_REQUEST_FILE` > `PROJECT_REQUEST.md` in the current directory; blank
  environment values count as unset and a blank `--request` is an error. Combining `--request` with `--request-file`
  now merges them instead of ignoring the file.
- The `new` recipe's `spec` stage is run by the new `product_analyst` instead of the solution architect, and every
  pipeline teammate can use `Search Docs`.
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

- A running command's log file now shows its output as it is produced instead of when 8 KB has been written or the
  command ends.
- Python commands no longer write bytecode caches, so an edit followed by a re-run within the same second is not
  served from stale bytecode.
- An inline `ENGINEERING_PROJECT_REQUEST` no longer overrides an explicit `--request-file`.
- Installed copies no longer look for a request file or create workspaces next to `site-packages`.
- `engineering-team` no longer exits with status 1 after a successful run (the crew result was being
  passed to `sys.exit`).

### Security

- The Docker sandbox (above) keeps commands away from your other files, your API keys, and the network;
  `docs/SAFETY.md` says what it does not protect against.
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
