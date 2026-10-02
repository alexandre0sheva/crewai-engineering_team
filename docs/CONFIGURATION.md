# Configuration

Every setting has one home here. Inspect what is in effect, and where each value came from,
with:

```bash
uv run engineering-team config show          # add --json, --provider, --profile, --config
```

Secrets are never printed: API keys are reported only as `set` or `MISSING`, and credentials
inside URLs are masked.

## Where settings come from

Highest precedence first:

1. **Command-line options** (`--provider`, `--profile`, `--project-name`, `--workspace-root`, …)
2. **Environment variables** (`ENGINEERING_*`; a `.env` file is loaded too). Blank values count as unset.
3. **Project config**: `engineering-team.toml` in the current directory (or the file given by
   `--config` / `ENGINEERING_CONFIG_FILE`, which replaces it).
4. **User config**: `$XDG_CONFIG_HOME/engineering-team/config.toml` (default
   `~/.config/engineering-team/config.toml`).
5. **Built-in defaults.**

Nested tables merge across layers. Unknown keys and invalid values fail immediately with a one-line
message that names the setting and the layer it came from. A request containing the smoke marker
(`<!-- ENGINEERING_TEAM_PROFILE: smoke -->`) selects the `smoke` profile **only** when no layer chose
a profile.

## Settings reference

| Key (TOML) | Environment variable | Default | Meaning |
|---|---|---|---|
| `provider` | `ENGINEERING_PROVIDER` | `openai` | Model preset: `openai`, `anthropic`, `google`, `ollama` (`azure` needs `enable_azure`) |
| `profile` | `ENGINEERING_RUN_PROFILE` | `standard` | `standard`, `smoke` (cheap end-to-end check), `max-quality` |
| `project_name` | `ENGINEERING_PROJECT_NAME` | `mvp-app` | Workspace directory name |
| `workspace_root` | `ENGINEERING_WORKSPACE_ROOT` | `workspace` | Parent of generated projects (relative → current directory) |
| `request` | `ENGINEERING_PROJECT_REQUEST` | – | Inline request used when no `--request`/`--example`/`--request-file` is given |
| `request_file` | `ENGINEERING_REQUEST_FILE` | – | Request file used when nothing above it is given (then `./PROJECT_REQUEST.md`) |
| `verbose` | `ENGINEERING_VERBOSE` | `true` | CrewAI console detail |
| `tracing` | `ENGINEERING_TRACING` | `false` | Opt-in CrewAI tracing |
| `docs_mcp_urls` | `ENGINEERING_DOCS_MCP_URLS` | – | Comma-separated documentation MCP servers for specialists (not used in `smoke`) |
| `command_allowlist` | `ENGINEERING_COMMAND_ALLOWLIST` | – | Extra executables project commands may run (keep narrow) |
| `subprocess_env_allowlist` | `ENGINEERING_SUBPROCESS_ENV_ALLOWLIST` | – | Environment variable names project commands may inherit |
| `ollama_base_url` | `ENGINEERING_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `enable_azure` | `ENGINEERING_ENABLE_AZURE` | `false` | Allow the Azure provider and `azure/...` models |
| `execution.backend` | `ENGINEERING_EXECUTION_BACKEND` | `local` | `local` (Docker arrives later in 0.2.0) |
| `execution.max_parallel_commands` | `ENGINEERING_MAX_PARALLEL_COMMANDS` | `2` | Concurrent project commands |
| `parallel.max_parallel_agents` | `ENGINEERING_MAX_PARALLEL` | `3` | Concurrent agents (used once parallel execution lands) |
| `parallel.max_rpm` | `ENGINEERING_MAX_RPM` | – | Requests-per-minute cap shared by agents |
| `budget.max_cost_usd` | `ENGINEERING_BUDGET_MAX_COST_USD` | – | Reserved: enforcement arrives with usage accounting |
| `budget.max_tokens` | `ENGINEERING_BUDGET_MAX_TOKENS` | – | Reserved |
| `budget.max_wall_seconds` | `ENGINEERING_BUDGET_MAX_WALL_SECONDS` | – | Reserved |
| `budget.max_tool_calls` | `ENGINEERING_BUDGET_MAX_TOOL_CALLS` | – | Reserved |
| `budget.max_repair_rounds` | `ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS` | `3` | Reserved |

Provider credentials are read by the model SDKs from their usual variables and are **not** settings:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` (or `GEMINI_API_KEY`); Azure uses its own
variables. A missing key is reported before a run starts (preparation with `--prepare-only` needs none).

## Models

Each role (the manager `engineering_lead` and every specialist) resolves to a model in this order,
later steps winning:

1. the **provider preset** for the role's tier in the active profile,
2. `[models.tiers.<tier>]` overrides,
3. `[profiles.<profile>.lead|worker]` overrides,
4. `[models.roles.<role>]` overrides.

Tiers are `max`, `lead`, `reviewer`, `worker`, `cheap`. Profiles pick them:

| Profile | Lead tier / iterations | Worker tier / iterations | Docs MCP |
|---|---|---|---|
| `standard` | `lead` / 35 | `worker` / 30 | yes |
| `smoke` | `worker` / 18 | `cheap` / 14 | no |
| `max-quality` | `max` / 50 | `lead` / 40 | yes |

### Provider presets

Presets are organised **by price**: a tier never costs less than the tier below it, and only models
worth their price are included. Prices (USD per million input/output tokens) and IDs were checked
against the providers' documentation on 2026-10-02; they change often, so treat the presets as
starting points and override per tier or role. They are *defaults*, not measured recommendations
(the benchmark work in 0.2.0 will tune them). `config show` prints the price of every resolved model.

| Model | Price (in / out) | Role in the ladder |
|---|---|---|
| `openai/gpt-6-luna` | $0.10 / $0.50 | cheapest: high-volume specialist work |
| `gemini/gemini-3.8-flash` | $0.75 / $3.75 (introductory, until 2026-12-31; $1.50 / $7.50 after) | low-cost generalist |
| `openai/gpt-6.1-sol` | $2 / $10 | flagship manager and reviewer (same price as Sonnet 5.5) |
| `anthropic/claude-sonnet-5-5` | $2 / $10 | flagship manager and reviewer |
| `anthropic/claude-opus-5-5` | $4 / $20 | most expensive: the `max` tier only |
| `ollama/qwen3.8:27b` | free (local) | best open model up to 30B in the Ollama library |

| Tier | `openai` | `anthropic` | `google` | `ollama` |
|---|---|---|---|---|
| `max` | `openai/gpt-6.1-sol` (xhigh) | `anthropic/claude-opus-5-5` | `gemini/gemini-3.8-flash` | `ollama/qwen3.8:27b` |
| `lead` | `openai/gpt-6.1-sol` (high) | `anthropic/claude-sonnet-5-5` | `gemini/gemini-3.8-flash` | `ollama/qwen3.8:27b` |
| `reviewer` | `openai/gpt-6.1-sol` (medium) | `anthropic/claude-sonnet-5-5` | `gemini/gemini-3.8-flash` | `ollama/qwen3.8:27b` |
| `worker` | `openai/gpt-6-luna` (low) | `anthropic/claude-sonnet-5-5` | `gemini/gemini-3.8-flash` | `ollama/qwen3.8:27b` |
| `cheap` | `openai/gpt-6-luna` (none) | `anthropic/claude-sonnet-5-5` | `gemini/gemini-3.8-flash` | `ollama/qwen3.8:27b` |

- OpenAI uses exactly two models (Sol for judgement, Luna for volume); Anthropic exactly Sonnet and
  Opus; Google exactly Gemini 3.8 Flash. Anthropic has nothing cheaper than Sonnet, so its workers cost
  the same as its manager; Opus is used only by the `max-quality` profile.
- **Ollama** needs a running server (`ollama pull qwen3.8:27b`, 18 GB, 256K native context). The
  context window is capped at 32K for CrewAI because Ollama's server-side `num_ctx` is usually far
  smaller than the model's; raise `context_window` if you configure the server for more.
- **Azure is disabled by default.** Enable it with `enable_azure = true` (or `ENGINEERING_ENABLE_AZURE=true`),
  then give the deployment names (Azure model IDs) per tier, e.g. `azure/my-gpt-deployment`. With Azure
  disabled, `provider = "azure"` and any `azure/...` model are rejected with a one-line explanation.
- **Anthropic, Google, Azure** need their SDK: `uv sync --extra anthropic` (or `google`, `azure`; or
  `pip install "engineering_team[anthropic]"`). A missing SDK is reported up front with this hint.
- Models can be mixed across providers because model strings are `provider/model-id`; any other
  CrewAI-supported model can be set through the override tables (it just is not a preset).
- **Reasoning effort** (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`) is applied to OpenAI and
  Azure models only; Anthropic and Gemini use their own adaptive thinking and ignore it (`config show`
  marks such values "ignored by this provider").
- **GPT-6 models** are called through the Responses API (`gpt-6.1-sol` has no tool calling on Chat
  Completions) and get an explicit 1.05M-token context window, because CrewAI would otherwise assume
  about 7K for IDs it does not know. Override with `api` / `context_window` on any model table.

### Example `engineering-team.toml`

```toml
provider = "anthropic"
profile = "standard"

[parallel]
max_parallel_agents = 4

[models.tiers.worker]
model = "anthropic/claude-sonnet-5-5"

[models.roles]
quality_engineer = "anthropic/claude-opus-5-5"

[models.roles.backend_engineer]
model = "openai/gpt-6-luna"
reasoning_effort = "medium"
max_iter = 20
```

Model tables accept `model`, `reasoning_effort`, `temperature` (0–2), `max_iter`, `context_window` and
`api` (`completions` or `responses`).

### Environment variables from 0.1.0

All of them still work. The standard names configure the `standard` profile only and the
`ENGINEERING_SMOKE_*` names the `smoke` profile only, so production settings can never make a smoke
run expensive:

| Standard profile | Smoke profile | Sets |
|---|---|---|
| `ENGINEERING_LEAD_MODEL` | `ENGINEERING_SMOKE_LEAD_MODEL` | `profiles.<profile>.lead.model` |
| `ENGINEERING_LEAD_REASONING_EFFORT` | `ENGINEERING_SMOKE_LEAD_REASONING_EFFORT` | `…lead.reasoning_effort` |
| `ENGINEERING_LEAD_MAX_ITER` | `ENGINEERING_SMOKE_LEAD_MAX_ITER` | `…lead.max_iter` |
| `ENGINEERING_WORKER_MODEL` | `ENGINEERING_SMOKE_WORKER_MODEL` | `profiles.<profile>.worker.model` |
| `ENGINEERING_WORKER_REASONING_EFFORT` | `ENGINEERING_SMOKE_WORKER_REASONING_EFFORT` | `…worker.reasoning_effort` |
| `ENGINEERING_WORKER_MAX_ITER` | `ENGINEERING_SMOKE_WORKER_MAX_ITER` | `…worker.max_iter` |

If you set these variables to models from an older release, they keep working as plain overrides. The
0.2.0 presets no longer include the superseded GPT-5.6 models, which OpenAI's model documentation does
not list any more.

Other settings for command safety and the run directory are described in the
[README](../README.md#filesystem-and-command-safety) and [ARCHITECTURE](ARCHITECTURE.md).
