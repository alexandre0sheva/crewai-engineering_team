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

### Changed

- Upgraded CrewAI to 1.15.23 (supported range `>=1.15.23,<1.16`) and refreshed the lockfile
  and dev tools (pytest 9.1, Ruff 0.16).
- The test suite is hermetic: it ignores the developer's `.env`, uses throw-away framework
  storage and a dummy API key, and runs from a temporary directory.

### Fixed

### Security

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
