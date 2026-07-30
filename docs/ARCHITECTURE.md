# Orchestrator architecture

## Why hierarchical

The crew uses CrewAI's hierarchical process with a custom
`engineering_lead`. The lead runs on the quality-first model tier, delegates
each task to a specialist based on role and current project state, and validates
the result before the next task starts. Specialists use the lower-cost worker
tier.

CrewAI 1.15.9 requires a custom hierarchical manager to be constructed without
ordinary tools. At task execution time CrewAI supplies the lead with scoped
delegation and coworker-question tools. Filesystem, command, and optional MCP
tools belong only to specialists, so workspace inspection and corrections are
explicit delegated work whose evidence is returned to the lead.

The process is deliberately staged:

1. Architecture and acceptance criteria
2. Project foundation and first vertical slice
3. Backend, domain, integration, or CLI implementation
4. Frontend or primary user interaction
5. Independent quality and security verification
6. Release-readiness review

Tasks share both explicit CrewAI context and a persistent filesystem. Task
summaries are useful handoffs, while the workspace remains the source of truth.

## Filesystem model

Generated applications live under `workspace/<project-name>/` by default. A run
resumes that directory unless `--reset` is explicitly used. This supports normal
nested project structures and lets agents use the stack's own build and test
tools.

All specialist file APIs:

- accept only relative paths;
- reject `..`, absolute paths, `.git`, and symlink escapes;
- limit text read/write sizes;
- support exact replacement checks;
- protect the workspace root from deletion.

Commands run without a shell from the project directory. The command allowlist
supports common language runtimes, package managers, test runners, and build
tools. Shell operators and inline code flags are rejected. The child process
gets an isolated home/cache under `.engineering-team/`, and secrets are removed
unless a variable is explicitly named in
`ENGINEERING_SUBPROCESS_ENV_ALLOWLIST`.

This is a strong project boundary, not process isolation. Package scripts and
generated programs are executable code. Run the orchestrator inside a container
or VM when the request or dependency set is untrusted.

## Model routing

Defaults:

- Lead: `openai/gpt-5.6-sol`, reasoning effort `high`
- Specialists: `openai/gpt-5.6-terra`, reasoning effort `low`

Every value is configurable through environment variables. The architecture
preserves the quality/cost distinction rather than using the flagship model for
every tool call. Optional documentation MCP servers are disabled by default so
the crew has no hidden network dependency.

The bundled `PROJECT_REQUEST.md` carries a smoke-profile marker. For that
example, the lead uses Terra/low, specialists use Luna/none, iteration caps are
lower, and documentation MCP servers stay disabled. A CLI `--profile` flag or
`ENGINEERING_RUN_PROFILE` overrides marker detection. Standard and smoke modes
have separate model/reasoning/iteration override variables so full-quality
settings cannot accidentally make a smoke run expensive.

## Local state

Each generated app contains `.engineering-team/` with the normalized request,
run metadata, local command homes/caches, and CrewAI execution log. It should not
contain credentials. Product code, tests, docs, and its own README remain at the
normal project root.
