# Universal MVP Engineering Team

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
- Filesystem tools prevent traversal and symlink escapes.
- Command execution returns stdout, stderr, exit code, and timeouts; it uses no
  shell and strips secrets from child processes.
- Local artifact guardrails require architecture, README, verification, and
  release documents before tasks can pass.
- Tracing and remote documentation MCPs are opt-in.

## Requirements

- Python 3.10–3.13
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

The defaults use:

```dotenv
ENGINEERING_LEAD_MODEL=openai/gpt-5.6-sol
ENGINEERING_LEAD_REASONING_EFFORT=high
ENGINEERING_WORKER_MODEL=openai/gpt-5.6-terra
ENGINEERING_WORKER_REASONING_EFFORT=low
```

Use any CrewAI-supported provider/model strings if you prefer another routing
strategy. Keep the lead on your quality-first tier and workers on a balanced
lower-cost tier.

## Quick start

`PROJECT_REQUEST.md` contains a concrete Tiny Notes CLI project, so this command
works immediately:

```bash
crewai run
```

Its smoke marker selects lower-cost defaults:

```text
lead:    openai/gpt-5.6-terra, reasoning low, max 18 iterations
workers: openai/gpt-5.6-luna,  reasoning none, max 14 iterations
```

The example has no third-party runtime dependencies, web research, graphical
interface, or integrations. The run exercises the manager, every specialist,
filesystem tools, tests, verification, and release handoff.

Standard model variables do not override smoke mode. If needed, smoke mode has
dedicated `ENGINEERING_SMOKE_*` overrides, so production settings cannot
accidentally make the bundled test expensive.

## Define your own MVP

Replace [PROJECT_REQUEST.md](PROJECT_REQUEST.md) with your real request and
remove its `ENGINEERING_TEAM_PROFILE: smoke` marker. Requests without that
marker use the standard Sol/Terra profile. You can also pass a different file:

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

`crewai run` reads `PROJECT_REQUEST.md` and uses `mvp-app` as the default
workspace name. Set `ENGINEERING_PROJECT_NAME` in `.env` to change it.

The same command resumes the existing project. To intentionally start that
project over:

```bash
uv run engineering-team \
  --project-name habit-tracker \
  --request-file path/to/habit-tracker.md \
  --reset
```

`--reset` deletes only `workspace/habit-tracker/`, not the workspace root or this
orchestrator repository.

## Generated project layout

Each MVP owns a conventional project root:

```text
workspace/
└── habit-tracker/
    ├── .engineering-team/
    │   ├── request.md
    │   ├── run.json
    │   └── crew-log.json
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
are git-ignored by this orchestrator; initialize a separate repository inside a
finished MVP if you want to keep it.

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

Specialist agents can read, create, edit, replace, list, and delete files only
inside the active generated project. The lead coordinates these operations by
delegation. Paths are relative and checked after symlink resolution. `.git`,
absolute paths, traversal, and deleting the workspace root are blocked.

Development commands:

- run from the generated project;
- use an executable allowlist;
- do not use a shell;
- reject pipes, redirection, chaining, and inline code flags;
- cap execution time;
- return stdout, stderr, and exit status;
- use local caches under `.engineering-team/`;
- do not inherit API keys or tokens by default.

Add a required executable narrowly:

```dotenv
ENGINEERING_COMMAND_ALLOWLIST=just,flutter
```

Allow a generated program to receive a specific environment variable only when
necessary:

```dotenv
ENGINEERING_SUBPROCESS_ENV_ALLOWLIST=DATABASE_URL
```

This boundary reduces accidental damage; it is not a VM security boundary.
Package lifecycle scripts and generated programs are executable code. Run the
whole orchestrator in a container or VM for untrusted requests or dependencies.

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
the generated project's `docs/verification.md` and `docs/release-report.md`.

## Useful environment variables

| Variable | Purpose |
| --- | --- |
| `ENGINEERING_PROJECT_REQUEST` | Inline request used when CLI input is absent |
| `ENGINEERING_REQUEST_FILE` | Default request-file path |
| `ENGINEERING_PROJECT_NAME` | Default generated project name |
| `ENGINEERING_WORKSPACE_ROOT` | Parent directory for generated apps |
| `ENGINEERING_RUN_PROFILE` | `standard` or lower-cost `smoke` routing |
| `ENGINEERING_LEAD_MODEL` | Manager model |
| `ENGINEERING_WORKER_MODEL` | Specialist model |
| `ENGINEERING_LEAD_REASONING_EFFORT` | Manager reasoning effort |
| `ENGINEERING_WORKER_REASONING_EFFORT` | Specialist reasoning effort |
| `ENGINEERING_SMOKE_*` | Optional smoke-only model, reasoning, and iteration overrides |
| `ENGINEERING_VERBOSE` | CrewAI console detail |
| `ENGINEERING_TRACING` | Opt-in CrewAI tracing |
| `ENGINEERING_DOCS_MCP_URLS` | Optional comma-separated MCP URLs |
| `ENGINEERING_COMMAND_ALLOWLIST` | Extra command executable names |
| `ENGINEERING_SUBPROCESS_ENV_ALLOWLIST` | Environment names passed to project commands |

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
