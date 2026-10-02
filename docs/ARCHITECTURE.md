# Orchestrator architecture

## Why hierarchical

The crew uses CrewAI's hierarchical process with a custom
`engineering_lead`. The lead runs on the quality-first model tier, delegates
each task to a specialist based on role and current project state, and validates
the result before the next task starts. Specialists use the lower-cost worker
tier.

CrewAI 1.15 requires a custom hierarchical manager to be constructed without
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

Generated applications live under `workspace/<project-name>/` (relative to the
directory the command is run from) by default. A run resumes that directory unless
`--reset` is explicitly used. A project is *owned* when it contains
`.engineering-team/owner.json`; non-empty directories without it are never written
to, and `--reset` deletes only owned projects (`--force-reset` overrides for foreign
directories but never for home, the current directory and its parents, the filesystem
root, symlinks, or the engineering-team installation). This supports normal
nested project structures and lets agents use the stack's own build and test
tools.

All specialist file APIs:

- accept only relative paths;
- reject `..`, absolute paths, and symlink escapes;
- reject `.git` and the controller-owned `.engineering-team/` directory (except its
  `tmp/` scratch space) **after** symlink resolution and case-insensitively, so aliases
  cannot reach them;
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

## Settings and model routing

`settings.py` is the only module that reads configuration. It builds an immutable, typed
`Settings` object from layered sources (CLI overrides > environment > project TOML > user TOML >
defaults), remembers which layer set every value, and never mutates `os.environ`. The profile (for
example `smoke`, chosen by CLI, environment, config file, or the request's marker) travels inside
`Settings`.

`model_routing.py` is pure data: provider presets mapping tiers (`max`, `lead`, `reviewer`,
`worker`, `cheap`) to model IDs, model facts that CrewAI cannot discover (context window, whether a
model needs the Responses API), and the profile definitions. `Settings.resolve_model(role)` layers
preset, tier, profile, and per-role overrides into a `ResolvedModel`; `crew.build_llm` turns that
into a CrewAI `LLM`, sending only parameters the provider accepts (reasoning effort is OpenAI/Azure
only). The architecture preserves the quality/cost distinction: the lead runs on the quality tier
and specialists on a cheaper one rather than using the flagship model for every tool call. Optional
documentation MCP servers are off by default, so the crew has no hidden network dependency.

Defaults, tiers, profiles, and every setting are documented once in
[CONFIGURATION.md](CONFIGURATION.md).

The bundled `tiny-notes` example request (`--example tiny-notes`) carries a smoke-profile marker,
which selects the `smoke` profile unless a profile was chosen explicitly.

## Local state

Each generated app contains `.engineering-team/` with the normalized request,
run metadata, local command homes/caches, and CrewAI execution log. It should not
contain credentials. Product code, tests, docs, and its own README remain at the
normal project root.
