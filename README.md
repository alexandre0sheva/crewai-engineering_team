# Universal MVP Engineering Team

[![CI](https://github.com/alexandre0sheva/crewai-engineering_team/actions/workflows/ci.yml/badge.svg)](https://github.com/alexandre0sheva/crewai-engineering_team/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue.svg)

A reusable CrewAI project that turns a product request into a tested MVP in a
persistent, normal project directory. A high-capability engineering lead
orchestrates lower-cost architecture, backend, frontend, and quality specialists.

The team is stack-agnostic. It can build web apps, APIs, CLIs, automations, data
tools, mobile-oriented projects, or other small products when the required
runtime is available locally. The generated application can use any suitable
stack and a conventional nested project structure.

## Capabilities

- Product requests can come from a Markdown file, CLI argument, or environment
  variable.
- A custom `engineering_lead` manages a hierarchical CrewAI process and validates
  every task.
- Model tiers are explicit and configurable: flagship lead, lower-cost workers.
- Four stack-agnostic specialists cover architecture, backend, frontend, and
  quality.
- Six tasks cover architecture, foundation, backend/core, frontend/experience,
  verification, and release review.
- Generated projects use conventional nested files under
  `workspace/<project-name>/`.
- Workspaces persist across runs by default; reset is explicit.
- Filesystem tools prevent traversal and symlink escapes, and keep agents out of `.git` and
  the orchestrator's own `.engineering-team/` state.
- Command execution returns stdout, stderr, exit code, and timeouts; it uses no
  shell and strips secrets from child processes.
- Interim artifact guardrails require architecture, README, verification, and
  release documents to exist and contain real content before tasks can pass.
- Tracing and remote documentation MCPs are opt-in.

## Requirements

- Python 3.11–3.13 (CrewAI does not yet support 3.14)
- [uv](https://docs.astral.sh/uv/)
- An API key for the configured model provider
- Any language runtimes required by the MVP you ask the team to build

The supported CrewAI version range is declared in `pyproject.toml`.

## Setup

```bash
uv sync --group dev
cp .env.example .env
```

Add `OPENAI_API_KEY` to `.env`. Never commit `.env`.

Models, profiles, budgets, and every `ENGINEERING_*` variable are documented in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md); `uv run engineering-team config show` prints what
is in effect and where each value came from. The default provider is OpenAI; Anthropic, Google,
Ollama, and Azure presets are available with `--provider` (or `provider = ...` in
`engineering-team.toml`). Keep the lead on your quality-first tier and workers on a balanced
lower-cost tier.

## Quick start

The package bundles a concrete Tiny Notes CLI request, so this command works
immediately from any directory (projects are created under `./workspace/`):

```bash
uv run engineering-team --example tiny-notes
```

Its smoke marker selects the lower-cost `smoke` profile (a cheap model for every role, low
reasoning, and small iteration caps; see [docs/CONFIGURATION.md](docs/CONFIGURATION.md#models)).

The example has no third-party runtime dependencies, web research, graphical
interface, or integrations. The run exercises the manager, every specialist,
filesystem tools, tests, verification, and release handoff.

Standard model variables never override smoke mode, so production settings cannot
accidentally make the bundled test expensive.

## Define your own MVP

Write your real request as a Markdown file (start from the bundled example if you
like; remove its `ENGINEERING_TEAM_PROFILE: smoke` marker). Requests without that
marker use the `standard` profile. Pass the file explicitly:

```bash
uv run engineering-team \
  --project-name habit-tracker \
  --request-file path/to/habit-tracker.md \
  --profile standard
```

You can also pass a short request inline:

```bash
uv run engineering-team \
  --project-name webhook-inspector \
  --request "Build a local web app that receives, stores, filters, and replays webhook payloads."
```

For a strong result, include the problem, primary user and journey, must-have
scope, non-goals, acceptance criteria, data/privacy rules, integrations, and
deployment target. Technical choices are optional—the architect will choose
simple defaults when they are not specified.

## Run

Validate the request and prepare its project directory without spending model
tokens:

```bash
uv run engineering-team \
  --project-name habit-tracker \
  --request-file path/to/habit-tracker.md \
  --prepare-only
```

Run the full team:

```bash
uv run engineering-team \
  --project-name habit-tracker \
  --request-file path/to/habit-tracker.md
```

The request is taken from the first of these that is present: `--request`,
`--example`, `--request-file`, `ENGINEERING_PROJECT_REQUEST`,
`ENGINEERING_REQUEST_FILE`, then `PROJECT_REQUEST.md` in the current directory. So
`crewai run` works when your project directory contains a `PROJECT_REQUEST.md`; its
workspace is named `mvp-app` unless you set `ENGINEERING_PROJECT_NAME` (see
[docs/CONFIGURATION.md](docs/CONFIGURATION.md)).
Relative workspace roots, including the default `workspace`, are resolved against the
directory you run the command from.

The same command resumes the existing project. To intentionally start that
project over:

```bash
uv run engineering-team \
  --project-name habit-tracker \
  --request-file path/to/habit-tracker.md \
  --reset
```

`--reset` deletes only `workspace/habit-tracker/`, and only if this tool created it
(it contains `.engineering-team/owner.json`; projects from 0.1.0 are recognised too).
An existing non-empty directory that this tool did not create is never modified or
reset by default; `--force-reset` overrides that, but your home directory, the
current directory and its parents, the filesystem root, symlinks, and the
engineering-team installation are always refused.

Exit codes: `0` success (verified, for a pipeline run), `2` usage or configuration error (one-line
message), `1` runtime failure, `3` the controller's own checks failed, `4` verification was partial (a
required check could not run), `130` interrupted or cancelled.

### Staged runs: resume and cancel

`--strategy pipeline` (or `ENGINEERING_STRATEGY=pipeline`) runs the team as a staged,
resumable pipeline (spec, plan, foundation, implement, verify, release) instead of the default
manager-led crew; `--strategy single` runs one agent with every tool, the baseline the other
strategies are measured against. In a pipeline run the controller, not an agent, runs the tests and checks
(`--checks FILE` adds your own; see [Configuration](docs/CONFIGURATION.md#verification-verify-and---checks)) and
writes `docs/verification.md` from what it saw. A pipeline run that was cancelled (Ctrl-C), interrupted, or failed
continues where it stopped, without redoing finished stages:

```bash
uv run engineering-team resume <run_id> --project-name habit-tracker
uv run engineering-team cancel <run_id> --project-name habit-tracker   # from another terminal
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#pipeline-recipes-and-resume) for how stages,
recipes, and resume work.

## Generated project layout

Each MVP owns a conventional project root:

```text
workspace/
└── habit-tracker/
    ├── .engineering-team/
    │   └── runs/<run-id>/        # manifest, event log, request, logs per run
    ├── docs/
    │   ├── architecture.md
    │   ├── implementation-plan.md
    │   ├── verification.md
    │   └── release-report.md
    ├── README.md
    ├── src/ or apps/ or packages/
    ├── tests/
    └── stack-specific configuration
```

The exact source tree is chosen for the product and stack. Generated workspaces
are git-ignored by this orchestrator. A `pipeline` or `single` run makes a new project its own Git
repository (an initial commit, then one commit per finished stage; `--no-git` skips it); for other runs,
initialize a separate repository inside a finished MVP if you want to keep it.

## How orchestration works

CrewAI's hierarchical process gives the custom lead three responsibilities:

1. Assign each stage to the specialist best suited to the project state.
2. Supply context and reconcile decisions across specialists.
3. Validate task output and require rework when acceptance criteria or required
   artifacts are missing.

CrewAI 1.15 requires a custom hierarchical manager to start without ordinary
tools. The lead therefore receives CrewAI's scoped delegation and coworker tools,
while specialists receive the project filesystem, command, and optional
documentation tools. When the lead needs a file inspected or corrected, it
delegates that concrete action and evaluates the specialist's evidence.

The worker pool contains:

- `solution_architect`
- `backend_engineer`
- `frontend_engineer`
- `quality_engineer`

Tasks do not hardcode an assignee. This lets the lead route backend-free apps,
CLI products, integration-heavy automations, or unusual stacks intelligently
according to the requirements and current workspace state.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for boundaries and design
details.

## Filesystem and command safety

Agents work only inside the generated project: paths are relative and checked after symlink
resolution, `.git` and the orchestrator's `.engineering-team/` state are off limits, and
commands run without a shell from an executable allowlist with a scrubbed environment and a
timeout that kills the whole process tree. This reduces accidental damage; it is not a VM
boundary, so run the orchestrator in a container or VM for untrusted requests or dependencies.
See [docs/SAFETY.md](docs/SAFETY.md) for the full model and
[docs/TOOLS.md](docs/TOOLS.md) for every tool.

Add a required executable narrowly, and pass an environment variable to generated programs only
when necessary:

```dotenv
ENGINEERING_COMMAND_ALLOWLIST=just,flutter
ENGINEERING_SUBPROCESS_ENV_ALLOWLIST=DATABASE_URL
```

## Optional documentation tools

Remote MCP servers are disabled by default. To give specialist agents a
documentation server:

```dotenv
ENGINEERING_DOCS_MCP_URLS=https://your-trusted-server.example/mcp
```

Only configure servers you trust. They add latency, network access, and their
own data-handling boundary.

## Verification

Test and lint the orchestrator itself:

```bash
uv run pytest
uv run ruff check .
```

Validate CLI setup without an LLM call:

```bash
uv run engineering-team \
  --project-name smoke-test \
  --request "Build a CLI that stores and lists notes in a local JSON file." \
  --prepare-only
```

An actual crew run consumes model tokens and may install dependencies selected
for the generated MVP. The quality and release stages record exact evidence in
the generated project's `docs/release-report.md`; in a pipeline run `docs/verification.md` is written by the
controller from checks it ran itself.

## CrewAI maintenance commands

Use these entry points for training, replay, and evaluation:

```bash
uv run train <iterations> <training-file> [request options]
uv run replay <task-id>
uv run test <iterations> <evaluation-model> [request options]
```

CrewAI evolves quickly. Before changing CrewAI-specific code, check the installed
version, PyPI, changelog, and relevant live documentation as required by
[AGENTS.md](AGENTS.md).
