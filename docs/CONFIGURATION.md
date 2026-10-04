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
   `ENGINEERING_OVERRIDES` takes a JSON object of dotted keys (`{"budget.max_cost_usd": 2,
   "team.qa.enabled": false}`) and ranks above the other variables; the web UI uses it to pass a run's options to the
   process it starts, and any key it names is validated like every other setting.
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
| `strategy` | `ENGINEERING_STRATEGY` | `pipeline` | How the team is orchestrated: `pipeline` (staged, resumable [Flow pipeline](ARCHITECTURE.md#pipeline-recipes-and-resume)), `hierarchical` (the 0.1.0 manager-led crew), or `single` (one agent with every tool, the benchmark baseline). `--strategy` overrides it. Chosen with the [benchmarks](BENCHMARKS.md#results-2026-10-04); only `new` follows it, the repository modes always use `pipeline`. |
| `team_profile` | `ENGINEERING_TEAM_PROFILE` | `full` | `full` runs every stage of the `new` recipe; `minimal` leaves out the optional ones (review, DevOps, docs), as `--profile smoke` does. See [Review](#review-review-and-team_profile) |
| `project_name` | `ENGINEERING_PROJECT_NAME` | `mvp-app` | Workspace directory name |
| `workspace_root` | `ENGINEERING_WORKSPACE_ROOT` | `workspace` | Parent of generated projects (relative → current directory) |
| `request` | `ENGINEERING_PROJECT_REQUEST` | – | Inline request used when no `--request`/`--example`/`--request-file` is given |
| `request_file` | `ENGINEERING_REQUEST_FILE` | – | Request file used when nothing above it is given (then `./PROJECT_REQUEST.md`) |
| `verbose` | `ENGINEERING_VERBOSE` | `true` | CrewAI console detail |
| `tracing` | `ENGINEERING_TRACING` | `false` | Opt-in CrewAI tracing |
| `docs_mcp_urls` | `ENGINEERING_DOCS_MCP_URLS` | – | Comma-separated MCP servers attached to every teammate with `mcp:docs`; kept as an alias of [`[mcp.<name>]`](#mcp-servers-mcpname) (no MCP is used in `smoke`) |
| `conventions_file` | `ENGINEERING_CONVENTIONS_FILE` | – | One more conventions file (a style guide) for any project; see [Repository conventions](#repository-conventions-conventions-and-conventions_file) |
| `allow_project_plugins` | `ENGINEERING_ALLOW_PROJECT_PLUGINS` | `false` | Load `.engineering-team/tools/*.py` as plugin tools. **Code execution**; see [Plugin tools](#plugin-tools-plugins-and-allow_project_plugins) |
| `command_allowlist` | `ENGINEERING_COMMAND_ALLOWLIST` | – | Extra executables project commands may run (keep narrow) |
| `subprocess_env_allowlist` | `ENGINEERING_SUBPROCESS_ENV_ALLOWLIST` | – | Environment variable names project commands may inherit |
| `ollama_base_url` | `ENGINEERING_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `enable_azure` | `ENGINEERING_ENABLE_AZURE` | `false` | Allow the Azure provider and `azure/...` models |
| `web.enabled` | `ENGINEERING_ALLOW_WEB` | `false` | Register the web tools (Web Search, Fetch URL, Package Info). Also `--allow-web`. Off means no network tool exists in the run; see [Web tools](#web-tools-web-and-knowledge) |
| `execution.backend` | `ENGINEERING_EXECUTION_BACKEND` | `local` | `local` runs commands on this machine; `docker` runs each in a hardened container (also `--sandbox docker`; never falls back to `local`). See [Docker sandbox](#docker-sandbox-executiondocker-and---sandbox) |
| `execution.max_parallel_commands` | `ENGINEERING_MAX_PARALLEL_COMMANDS` | `2` | Concurrent project commands |
| `parallel.max_parallel_agents` | `ENGINEERING_MAX_PARALLEL` | `3` | Work packages (and read-only reviewer jobs) that run at the same time, each in its own lane; also the board's in-progress limit. `1` runs packages one after the other, in dependency order, with no write scope (as a sequential run always did). See [Parallel execution](ARCHITECTURE.md#parallel-execution) |
| `parallel.max_rpm` | `ENGINEERING_MAX_RPM` | – | Model calls per minute, shared by every agent of a `pipeline` or `single` run (a sliding 60-second window; calls wait their turn and stop waiting on cancel). Unset: no cap. The `hierarchical` crew does not use it |
| `budget.max_cost_usd` | `ENGINEERING_BUDGET_MAX_COST_USD` | – | Stop the run after this many USD of estimated model cost (see [Budgets](#budgets-usage-and-cost)) |
| `budget.max_tokens` | `ENGINEERING_BUDGET_MAX_TOKENS` | – | Stop after this many prompt + completion tokens |
| `budget.max_wall_seconds` | `ENGINEERING_BUDGET_MAX_WALL_SECONDS` | – | Stop after this much wall-clock time |
| `budget.max_tool_calls` | `ENGINEERING_BUDGET_MAX_TOOL_CALLS` | – | Stop after this many agent tool calls |
| `budget.max_repair_rounds` | `ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS` | `3` | Fix-and-verify rounds the whole run may spend repairing checks the controller found failing (see [Verification](#verification-verify-and---checks)); `0` never calls a repair agent |

### Docker sandbox (`[execution.docker]` and `--sandbox`)

`--sandbox docker` (or `execution.backend = "docker"`) runs every command in a container; what that
does and does not protect is in [SAFETY.md](SAFETY.md#docker-backend). The settings below live in
the `execution.docker` table (config file only) and apply only to that backend.

| Setting | Default | Meaning |
|---------|---------|---------|
| `execution.docker.image` | – | Use this image for every command. Unset: one image per language, chosen from the program being run (`npm` → Node) and otherwise from the project's detected stack |
| `execution.docker.setup_image` | – | Image for commands that need the network (installs, audits). Unset: the same image as everything else |
| `execution.docker.git_image` | `alpine/git:2.54.0` | Image for the controller's Git commands (the language images lack Git) |
| `execution.docker.network` | `setup` | `setup`: only install-type commands (and servers that publish a port) get a network. `none`: no network for anything that does not publish a port, so installs fail |
| `execution.docker.memory` | `2g` | Memory limit per command (swap disabled); a number with `b`, `k`, `m`, or `g` |
| `execution.docker.cpus` | `2.0` | CPU limit per command |
| `execution.docker.pids_limit` | `512` | Maximum processes per command |
| `execution.docker.tmpfs_size` | `512m` | Size of the container's `/tmp` |

Default images (tags checked when they were chosen; override any with `image`): Python
`astral/uv:python3.12-bookworm-slim` (Python and `uv`), Node `node:22-slim`, Go `golang:1.25`,
Rust `rust:1-slim`, Java `eclipse-temurin:21-jdk`, .NET `mcr.microsoft.com/dotnet/sdk:9.0`,
Ruby `ruby:3.3-slim`, PHP `php:8.3-cli`. A missing image is pulled the first time it is needed. A
tool the image does not have (`mvn` on the Temurin image, say) fails with "not found"; set `image` to
one that has it.

```toml
[execution]
backend = "docker"

[execution.docker]
image = "python:3.12"
network = "setup"
memory = "4g"
cpus = 4
```

### Developer tools (`[tools.dev]`, config file only)

Applies to Run Tests, Run Linter, Type Check, Format Code, Build Project, Coverage Report, Install
Dependencies, and Dependency Audit ([TOOLS.md](TOOLS.md#developer-tools)). Timeouts are in seconds;
a tool call may ask for less but never more.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `tools.dev.test_timeout` | `300` | Run Tests, Rerun Failed Tests, Run Single Test |
| `tools.dev.lint_timeout` | `120` | Run Linter |
| `tools.dev.typecheck_timeout` | `180` | Type Check |
| `tools.dev.format_timeout` | `120` | Format Code |
| `tools.dev.build_timeout` | `300` | Build Project |
| `tools.dev.coverage_timeout` | `600` | Coverage Report (tests plus the report) |
| `tools.dev.audit_timeout` | `180` | Dependency Audit |
| `tools.dev.install_timeout` | `900` | Install Dependencies |
| `tools.dev.max_failures` | `20` | Failing tests a report lists (the rest are counted) |
| `tools.dev.max_diagnostics` | `50` | Diagnostics a lint, type-check, or build report lists (the rest are counted) |
| `tools.dev.extra_executables` | – | Extra executables the dev tools may run, on top of `command_allowlist` and the built-in dev tools (`eslint`, `prettier`, `golangci-lint`, `rubocop`, ...) |
| `tools.dev.allow_network` | `true` | Allow the two dev tools that need the network: Install Dependencies and Dependency Audit. Set `false` to refuse both |

```toml
[tools.dev]
test_timeout = 600
extra_executables = ["bazel"]
```

### Runtime tools and network (`[runtime]`, `[network]`, config file only)

Applies to the background-process, HTTP, port, and SQLite tools ([TOOLS.md](TOOLS.md#runtime-tools)).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `runtime.max_background_processes` | `4` | Background processes running at once in a run (a start beyond it is refused) |
| `runtime.process_lifetime_seconds` | `1800` | Hard lifetime of a background process; it is killed when it passes |
| `runtime.max_http_response_chars` | `20000` | Characters of an HTTP response body shown to the agent |
| `network.http_allowlist` | – | Hosts the HTTP Request tool may call besides this run's own loopback ports: a name (`api.example.com`), `host:port`, or `*.example.com`. Each such request is logged as an `http.request` event with `external = true`. Allowlisted names are trusted; they are not checked for private addresses |

```toml
[runtime]
max_background_processes = 6

[network]
http_allowlist = ["staging.example.com", "localhost:11434"]
```

`pricing` (config file only) overrides model prices; see [Budgets, usage, and cost](#budgets-usage-and-cost).

Provider credentials are read by the model SDKs from their usual variables and are **not** settings:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` (or `GEMINI_API_KEY`); Azure uses its own
variables. A missing key is reported before a run starts (preparation with `--prepare-only` needs none).

### Web tools (`[web]` and `[knowledge]`)

Applies to `Web Search`, `Fetch URL`, `Package Info`, and `Search Docs` ([TOOLS.md](TOOLS.md#web-and-knowledge-tools)).
**Off by default**: with `web.enabled = false` the three web tools are not even registered. Turn
them on per run with `--allow-web` or `ENGINEERING_ALLOW_WEB=true`, or in the config file.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `web.enabled` | `false` | The switch described above (environment variable `ENGINEERING_ALLOW_WEB`, flag `--allow-web`) |
| `web.roles` | – | Only these teammates get the web tools (names such as `researcher`, case-insensitive); empty means every teammate |
| `web.search_provider` | – | `serper`, `brave`, or `tavily`; unset picks the first provider whose key is set, in that order |
| `web.allow_domains` | – | When set, `Fetch URL` may only reach these hosts (`docs.python.org` or `*.example.com`; the wildcard is for subdomains) |
| `web.deny_domains` | – | Hosts `Fetch URL` never reaches (checked on every redirect hop; deny beats allow) |
| `web.max_requests_per_run` | `40` | Outbound web requests the whole run may make; every redirect hop counts |
| `web.timeout_seconds` | `15` | Total time for one tool call, redirects included |
| `web.max_download_bytes` | `2000000` | Bytes read from a response (compressed data is decoded within the same cap) |
| `web.max_page_chars` | `20000` | Characters of a fetched page returned to the agent (a call may ask for fewer or up to 200,000) |
| `knowledge.context_dirs` | – | Extra directories `Search Docs` indexes (markdown, text, rst, adoc; symlinks are not followed; relative paths use the current directory) |

```toml
[web]
enabled = true
search_provider = "brave"
roles = ["researcher"]
allow_domains = ["docs.python.org", "*.readthedocs.io"]
max_requests_per_run = 25

[knowledge]
context_dirs = ["company-docs"]
```

**API keys are never settings.** Web Search reads its key from the environment, by these
names only: `SERPER_API_KEY`, `BRAVE_API_KEY`, `TAVILY_API_KEY`. `config show` lists which are
`set` (or `MISSING` when the web tools are enabled and none is), never a value, and the run's event
log scrubs them. A key is sent only to its own provider, in a request header.

### Browser tools (`[browser]`, config file only)

Applies to the headless-browser tools ([TOOLS.md](TOOLS.md#browser-tools)). They exist only when the
optional extra is installed (`uv sync --extra browser`, then `uv run playwright install chromium`).
Which pages the browser may open is not a setting of its own: this run's localhost ports by
default, and external sites only through `web.enabled` and `web.allow_domains` (above).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `browser.channel` | `chromium` | `chromium` (Playwright's own download), `chrome`, or `msedge` (an installed browser, nothing to download) |
| `browser.max_contexts` | `2` | Browsers (one incognito context per teammate) open at once; another teammate's `Browser Open` is refused until one closes |
| `browser.page_timeout_seconds` | `30` | Time one navigation or action may take |
| `browser.max_snapshot_chars` | `20000` | Characters of a `Browser Snapshot` returned (a call may ask for fewer) |
| `browser.max_console_entries` | `100` | Console and network log entries kept per browser (the counts are not capped) |

```toml
[browser]
channel = "chrome"
max_contexts = 3
```

### Verification (`[verify]` and `--checks`)

Applies to the `verify` stage of `--strategy pipeline` (see
[Verification](ARCHITECTURE.md#verification-and-repair)): the controller runs the project's
checks itself, repairs what fails (at most `budget.max_repair_rounds` times), and ends the run
`verified`, `failed` (exit code 3), or `partial` (exit code 4).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `verify.checks_file` | – | Your own checks (below). Set with `--checks FILE`; the file must be outside the project |
| `verify.static_required` | `false` | A failing linter or type checker fails the verification (otherwise it is reported but advisory) |
| `verify.smoke` | `true` | Start the plan's `run` command and see that it does not crash; no `run` command, no smoke check |
| `verify.smoke_seconds` | `10` | How long the smoke check watches: still running after this long counts as started |
| `verify.smoke_required` | `false` | A crashing smoke check fails the verification |
| `verify.timeout` | `300` | Seconds a command the plan declares may run (detected checks use `tools.dev.*_timeout`) |

Which checks run, per kind (setup, tests, lint, type check, build, run): your checks, then the
commands the architect's plan declares (`setup`, `test`, `lint`, `build`, `run`), then what is detected from the project's own files. A
required `tests` check always exists, so a project with no tests is `failed`, never `verified`.
A plan command the controller may not run (not on the allowlist, or needing a shell) is dropped
with a note in the report and the detected default runs instead.

**Your checks file** (`--checks checks.yaml`, kept outside the project so agents cannot edit it):

```yaml
checks:
  - id: unit                    # letters, digits and . _ : -
    name: Unit tests            # optional; defaults to the id
    command: pytest -q          # a string or a list; no shell operators
    kind: test                  # setup | test | lint | typecheck | build | smoke | custom (default)
    required: true              # default true; false: reported, never fails the run
    timeout: 120                # seconds (default 300)
    criteria: [AC-1, AC-2]      # acceptance criteria this check proves
  - id: checkout-flow           # a Playwright script you wrote; it must exist in the project before the run
    type: browser_script        # runs `python <script>` (.py) or `npx playwright test <script>`
    script: e2e/checkout.py
    criteria: [AC-3]
```

A check of a `kind` replaces the plan's and the detected checks of that kind; `custom` checks are
added. Your commands may use any program (the file is yours), but still no shell, and they run
from the project root (or `cwd:`). The file is validated when the run starts (a problem is
an exit-2 usage error with the line to fix) and **pinned**: a copy and its hash go to the run
directory, and every verification refuses to run if the copy changed (an altered check proves
nothing). A `browser_script` is pinned by hash too. `resume` takes the same file again only if it is
unchanged; a changed request starts a new run, which needs `--checks` again.

### Git (`[git]` and `--no-git`)

A new project (an empty workspace) becomes a Git repository with an initial commit, and the `pipeline`
and `single` strategies commit after every finished stage (see [Git](ARCHITECTURE.md#git)).
A project that already is a repository is continued on its current branch; a project that has
files but no repository is left alone. The controller never pushes, fetches, or touches remotes.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `git.enabled` | `true` | Also `ENGINEERING_GIT` and `--no-git` (which sets it to `false`): no repository is created and nothing is committed (the read-only Git tools still read a repository that exists) |
| `git.author_name` | `Engineering Team` | Author and committer name of the controller's commits |
| `git.author_email` | `engineering-team@users.noreply.github.com` | Their email (a no-reply address; set your own to attribute the commits) |
| `git.squash` | `false` | Also `feature --squash`: when a repository mode (`feature`) succeeds, its stage commits since the starting commit become one commit (`feature: <title>`) on the team's branch |

### Requirements intake (`[intake]` and the request options)

`new` takes the request from `--request TEXT`, one or more `--request-file FILE`, or stdin (`-`, as
`--request -` or `--request-file -`); a `--request` and files merge in that order, each under a
`## Request: <name>` header, and a single source is used as it is. `--example NAME` stands alone.
With none of these the request comes from `request`, then `request_file`, then `./PROJECT_REQUEST.md`.
Line endings are normalised, a still-unedited template (`init`) is refused, and the request hash used by
`resume` is the same whichever way the text arrived. `--context-dir DIR` copies reference documents
(`.md`, `.mdx`, `.rst`, `.txt`, `.adoc`) into the workspace for the team to search; the other keys cap them
and the clarifying questions. `--interactive` (never the default, and ignored when stdin is not a terminal)
lets the team ask those questions; see [Usage](USAGE.md#writing-a-request).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `intake.max_request_chars` | `60000` | Longest merged request; a longer one is refused (put reference material in `--context-dir`) |
| `intake.max_context_files` | `100` | Most documents a `--context-dir` may hold; more is an error, not a silent cut |
| `intake.max_context_bytes` | `2000000` | Most bytes of documents in a `--context-dir` |
| `intake.max_questions` | `5` | Questions the Product Analyst may put to you in one run |
| `intake.question_timeout_seconds` | `600` | How long each question waits for your answer before the team assumes |

### Team (`[team]` and `team_file`)

Teammates come from `config/agents.yaml`; these settings change them or add new ones. See
[TEAM.md](TEAM.md) for the model, the roster, and a tutorial. Both files are optional and are checked
before a run starts (a mistake is a usage error naming the teammate and the field).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `team_file` | – | A YAML file of teammates (key → fields). Unset: `.engineering-team/team.yaml` in the current directory, if it exists. A named file that does not exist is an error. |
| `team.<key>.role`, `team.<key>.goal`, `team.<key>.backstory` | built-in | The prompt; all three are required for a new key |
| `team.<key>.tier` | `worker` | `lead`, `worker`, `cheap`, or `reviewer` (model tier under the `standard` profile) |
| `team.<key>.tool_groups` | built-in; new: read-only | Tool groups, plus `mcp:docs` and `mcp:<name>` for each [`[mcp.<name>]`](#mcp-servers-mcpname) server, plus the groups [plugin tools](#plugin-tools-plugins-and-allow_project_plugins) declare |
| `team.<key>.allow_delegation` | built-in | `hierarchical` manager only |
| `team.<key>.max_iter` | profile default | Reasoning steps per task |
| `team.<key>.enabled` | `true` | `false` takes the teammate out of the roster |
| `team.<key>.modes` | all | `new`, `feature`, `fix`, `maintain` |
| `team.<key>.stages` | – | Extra stages it may be given work packages for |

```toml
[team.data_engineer]
role = "Data engineer for {project_name}"
goal = "Build the data layer."
backstory = "You model data carefully."
stages = ["implement"]

[team.frontend_engineer]
enabled = false
```

### MCP servers (`[mcp.<name>]`)

An MCP server gives agents tools the team does not ship (a docs index, a ticket tracker). Define each one
by name; a teammate gets it by listing `mcp:<name>` under `tool_groups`, or by being named in the
server's `roles`. Nothing connects until an agent first needs the server. Checked when the config is
loaded and when the team is built (no network): one of `url` and `command`, a valid address, known
teammates in `roles`.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `mcp.<name>.url` | – | A remote server, `http(s)://host/path`. Credentials in the URL are masked in `config show`. Exactly one of `url` and `command` |
| `mcp.<name>.transport` | `http` | For a `url`: `http` (streamable HTTP) or `sse` |
| `mcp.<name>.command` | – | A local server: the program to start (CrewAI starts it over stdio) |
| `mcp.<name>.args` | – | Arguments of the `command` |
| `mcp.<name>.env` | – | Extra environment variables for the `command` (table of name = value; values are hidden in `config show` and the run record) |
| `mcp.<name>.roles` | – | Teammate keys that get this server (in addition to those listing `mcp:<name>`) |
| `mcp.<name>.allow_tools` | all | Only these tool names of the server may be used |

```toml
[mcp.docs]
url = "https://docs.example.com/mcp"
roles = ["backend_engineer", "frontend_engineer"]
allow_tools = ["search_docs"]

[mcp.local_index]
command = "python"
args = ["-m", "my_index_server"]

[team.technical_writer]
tool_groups = ["fs_read", "fs_write", "search", "mcp:local_index"]
```

`docs_mcp_urls` (`ENGINEERING_DOCS_MCP_URLS`) still works: its servers go to every teammate with
`mcp:docs` (every built-in specialist except the codebase analyst), exactly as in 0.1.0.
The `smoke` profile attaches no MCP server at all. **Trust:** a server's answers are untrusted data, and
a `command` server is a program started on this machine with your privileges, outside the Docker
sandbox ([SAFETY.md](SAFETY.md#extensions-mcp-plugins-and-hooks)). Every run logs an
`extension.warning` for each attached server, and `engineering-team doctor` lists them. MCP servers
apply to `pipeline`, `single`, and `hierarchical` runs alike.

### Repository conventions (`[conventions]` and `conventions_file`)

When the team works on an existing project (`feature`, `fix`, `maintain`) it reads the repository's
`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, and `.editorconfig` (those that exist; symlinks are
skipped) once at the start and puts them into the context of every stage except the codebase analysis,
framed as guidance on style, layout, and tooling that cannot change an agent's tools, permissions, or
task. `conventions_file` adds one more file for *any* project, first in line (a style guide kept
elsewhere; relative paths use the current directory; a missing file is a usage error).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `conventions.enabled` | `true` | `false` turns all of it off, including `conventions_file` |
| `conventions.max_chars` | `12000` | Characters of all files together; files that do not fit are left out |
| `conventions.max_file_chars` | `6000` | Characters of any one file; a longer one is cut and marked |
| `conventions_file` | – | The extra file (also in the table above) |

The run log records a `conventions.loaded` event with each file's name, size, and whether it was cut.
New projects have no repository files to load: use `conventions_file` for them.

### Knowledge sources (`knowledge.sources`, opt-in)

`Search Docs` (offline, BM25) is always there. For questions where meaning matters more than exact
words you can also give agents CrewAI **knowledge sources**: your documents are split, embedded by a
provider you name, and searched by similarity. **Off unless `knowledge.sources` is set, and then
`knowledge.embedder` is required**, so the provider is never implicit. Embedding sends the document
text to that provider (nothing leaves the machine with `ollama`) and costs what that provider
charges; the OpenAI and Google providers also need their API key (`doctor` and a run check it).
CrewAI keeps the vectors in its own storage (`CREWAI_STORAGE_DIR`).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `knowledge.sources` | – | Files or directories of text documents (md, mdx, rst, txt, adoc; ≤ 50 documents, ≤ 1 MB in all; relative paths use the current directory) |
| `knowledge.embedder.provider` | – | `openai`, `ollama`, `google-generativeai`, or `voyageai` (required with `sources`) |
| `knowledge.embedder.model` | provider default | The embedding model |
| `knowledge.embedder.url` | – | `ollama` only: the embeddings endpoint |
| `knowledge.roles` | – | Only these teammates get the sources; empty means every teammate |

```toml
[knowledge]
sources = ["docs/handbook", "STYLE.md"]
roles = ["backend_engineer"]

[knowledge.embedder]
provider = "ollama"
model = "mxbai-embed-large"
```

### Plugin tools (`[plugins]` and `allow_project_plugins`)

Add an agent tool without changing this package: a Python function wrapped by `@plugin_tool` (the API
and an example are in `engineering_team.plugins`). It is found in two places:

- **Installed packages** that declare an entry point in the `engineering_team.tools` group. On by
  default (installing the package was your decision); `plugins.entry_points = false` turns it off.
- **The project's own `.engineering-team/tools/*.py`** (files starting with `_` are ignored; looked for
  in the directory you start the team from). **Off unless `allow_project_plugins = true`**: these files
  are executed, so a repository you cloned must not be able to turn them on for you.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `allow_project_plugins` | `false` | Load `.engineering-team/tools/*.py` (the table above; `ENGINEERING_ALLOW_PROJECT_PLUGINS`) |
| `plugins.entry_points` | `true` | Load plugin tools from installed packages |
| `plugins.disable` | – | Entry-point names or project file names (without `.py`) to skip |

Each tool declares its `group`: a built-in group (then it follows that group's rules; a `web` plugin is
off unless `web.enabled`) or a name of your own. A teammate gets the tool by listing that group under
`tool_groups`, so a custom tool and a teammate that uses it need only configuration:

```toml
allow_project_plugins = true

[team.release_manager]
role = "Release manager for {project_name}"
goal = "Prepare the release notes."
backstory = "You keep a careful changelog."
tool_groups = ["fs_read", "search", "release"]
```

with `.engineering-team/tools/changelog.py` defining a tool with `group="release"`. Plugin tools follow the
[tool design rules](TOOLS.md#plugin-tools): they appear in the catalogue after the built-ins, emit
`tool.call` events, return `ERROR:` for failures, and are checked at load time (name, group,
description length, type hints); a broken plugin is a usage error naming the file and the fix.
`engineering-team plugins list` shows what would load, what was found but not loaded, and prints the
[TOOLS.md](TOOLS.md) rows with `--markdown`. **A plugin is trusted code** that runs in the controller
process: the write scope, path protection, and the Docker sandbox do not apply to it
([SAFETY.md](SAFETY.md#extensions-mcp-plugins-and-hooks)). Plugin tools reach teammates of `pipeline` and
`single` runs; the `hierarchical` crew uses its fixed tool groups.

### Hooks (`[[hooks.before_stage]]`, `[[hooks.after_stage]]`, `[[hooks.on_finish]]`)

Run a command or call a webhook around the run: notifications, CI glue. Each hook sets exactly one of
`command` (a program and its arguments; no shell) or `url` (an http(s) webhook that gets a JSON `POST`).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `hooks.<event>.command` | – | Argument list, run in the directory you started the team from, with a minimal environment (`PATH`, `HOME`, ...) plus `ENGINEERING_HOOK_*` variables; `ENGINEERING_HOOK_PAYLOAD` is the whole JSON |
| `hooks.<event>.url` | – | Webhook address. Often a secret, so only its host is shown by `config show`, written to the run record (`settings.json`), or logged |
| `hooks.<event>.timeout_seconds` | `10` | Longest a hook may take (1–120); then it is killed |
| `hooks.<event>.stages` | all | `before_stage` and `after_stage` only: run for these stages |
| `hooks.<event>.statuses` | all | `after_stage` and `on_finish` only: run for these outcomes (`succeeded`, `failed`, `cancelled`, `interrupted`) |

The events are `before_stage` (each stage of a `pipeline` run, before it starts), `after_stage`
(with its status, also after a failure or cancel), and `on_finish` (every run strategy, once, after
the run has ended, including cancelled runs). The payload is
`{"text": "...", "event", "run_id", "project"}` plus `stage` and `status` where they apply, `error` on a
failure, and `duration_seconds` and `estimated_cost_usd` at the end; `text` is a one-line summary, which
is what a Slack incoming webhook displays.

```toml
[[hooks.on_finish]]
url = "https://hooks.slack.com/services/T000/B000/XXXX"
statuses = ["failed", "succeeded"]

[[hooks.after_stage]]
command = ["./scripts/notify.sh"]
stages = ["verify"]
statuses = ["failed"]
timeout_seconds = 30
```

A hook never changes the run: a failure, a timeout, or a program that cannot start is a `hook.ran`
event with `ok: false` (and a warning in the report), nothing more. Commands and payloads are scrubbed
of secret environment values; a command gets no provider keys, so one that needs a credential reads it from its own file (never put one in `command`, which `config show` prints). Hooks run on this machine whatever the execution backend, with your
privileges ([SAFETY.md](SAFETY.md#extensions-mcp-plugins-and-hooks)).

### Web UI (`[ui]`)

Limits of `engineering-team ui` ([USAGE.md](USAGE.md#web-ui)). The server's own options (`--host`, `--port`,
`--allow-remote`, `--demo`) are command-line only.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `ui.max_concurrent_runs` | `2` | Runs the UI keeps going at once (each is a process of its own). Another start is refused with HTTP 429 until one ends; the workspace lock still lets only one run write to a project. |
| `ui.max_upload_bytes` | `2000000` | The most bytes of uploaded request and reference files in one start request (HTTP 413 beyond it). |
| `ui.max_request_bytes` | `200000` | The most bytes of any other request body (HTTP 413 beyond it). |

### Review (`[review]` and `team_profile`)

After verification the `new` recipe runs read-only reviewers (the code reviewer and the security
engineer, side by side), then optional DevOps and documentation stages ([TEAM.md](TEAM.md)). Their
findings go to `docs/review.md`; see [Review and optional stages](ARCHITECTURE.md#review-and-optional-stages).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `review.fail_on` | `high` | Findings of this severity or higher (`low`, `medium`, `high`, `critical`) are sent to the repair agent (one round of `budget.max_repair_rounds`), then the project is verified again. Lower findings are only reported. |
| `team_profile` | `full` | `minimal` skips the review, DevOps, and docs stages (the smoke profile does too). To drop one of them, disable its teammates instead: `[team.code_reviewer] enabled = false` (the stage is skipped when none of its teammates is enabled). |

### Adopting existing projects (`[analysis]`)

How the codebase analysts split and report on an existing project (`analyze --deep` and the `map` stage of the
`adopt` recipe; [USAGE.md](USAGE.md#adopting-an-existing-project), [ARCHITECTURE.md](ARCHITECTURE.md#adopting-an-existing-project)).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `analysis.max_chunks` | `6` | At most this many chunks (top-level packages or directories, by size) are analysed side by side, each by one read-only analyst; more means more parallel model calls (also capped by `parallel.max_parallel_agents`) and a finer map. 1 to 20. |
| `analysis.context_chars` | `8000` | The size cap, in characters, of the codebase map put into agent context by later modes (cut at a line). 1,000 to 100,000. |

### Fixing bugs (`[fix]`)

How hard `engineering-team fix` tries to reproduce a bug before it stops and asks you
([USAGE.md](USAGE.md#fixing-a-bug), [ARCHITECTURE.md](ARCHITECTURE.md#fixing-a-bug)).

| Key (TOML) | Default | Meaning |
|---|---|---|
| `fix.max_repro_attempts` | `3` | How many times the debugger may try to write a test or script that fails because of the bug. Each try is checked by the controller, which runs the reproduction itself. When none fails, the run ends `needs-info` (exit 4) with questions. 1 to 6. |
| `fix.repro_timeout` | `120` | Seconds one run of the reproduction may take; a hang is not a reproduction. 5 to 1,800. |

### Maintenance (`[maintain]`)

Limits for `engineering-team maintain` ([USAGE.md](USAGE.md#maintaining-a-project)). The threshold that makes
`review` and `security-audit` exit 3 is `review.fail_on` above.

| Key (TOML) | Default | Meaning |
|---|---|---|
| `maintain.max_refactor_lines` | `800` | The most lines (added plus removed) a `refactor` may change; a larger change fails the `diff_size` policy and is handed back to be split. 10 to 100,000. |
| `maintain.upgrade_group_size` | `8` | How many dependency upgrades `upgrade-deps` applies together before it checks. A failing group is halved until the upgrade that broke the checks is found; `1` goes strictly one by one. 1 to 50. |

## Models

Each role (the manager `engineering_lead` and every specialist) resolves to a model in this order,
later steps winning:

1. the **provider preset** for the role's tier in the active profile,
2. `[models.tiers.<tier>]` overrides,
3. `[profiles.<profile>.lead|worker]` overrides,
4. `[models.roles.<role>]` overrides.

Tiers are `max`, `lead`, `reviewer`, `worker`, `cheap`. Profiles pick them:

| Profile | Lead tier / iterations | Worker tier / iterations | MCP servers |
|---|---|---|---|
| `standard` | `lead` / 35 | `worker` / 30 | yes |
| `smoke` | `worker` / 18 | `cheap` / 14 | no |
| `max-quality` | `max` / 50 | `lead` / 40 | yes |

### Provider presets

Presets are organised **by price**: a tier never costs less than the tier below it, and only models
worth their price are included. Prices (USD per million input/output tokens) and IDs were checked
against the providers' documentation on 2026-10-02; they change often, so treat the presets as
starting points and override per tier or role. They are *defaults*: only the OpenAI preset was
exercised by the benchmarks ([results](BENCHMARKS.md#results-2026-10-04); its model IDs worked end to
end on 2026-10-04), and the only change they led to is the architect's tier. The other providers are
untested starting points. `config show` prints the price of every resolved model.

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
strategy = "hierarchical"

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

## Budgets, usage, and cost

Every run records its token usage, and prints it when it finishes:

```text
Usage: 5,310 tokens (prompt 4,200, of which 300 cached; completion 1,110) in 14 model call(s) and 22 tool call(s)
Estimated cost: $0.0123
```

The same numbers are in `.engineering-team/runs/<run-id>/usage.json` (totals and a breakdown by
stage, agent, and model, with per-model cost) and in the manifest's `summary`. The cost uses the
price table in `src/engineering_team/data/pricing.toml`: USD per million tokens for each shipped
model, each row with its `source_url` and the date it was checked (`as_of`). Cached prompt tokens
and cache writes use their own rates where the provider has them; reasoning tokens are part of the
completion price. Prices change, so treat the cost as an estimate. **A model without a price has an
unknown cost**: the report says `Estimated cost: unknown (no price for ...)` and never shows `$0`.
If one of several models is unpriced the total is unknown too (`usage.json` still has the known part).

### Override or add a price

Prices belong to the model ID the run uses. Override one (or add a model that has none) in
`engineering-team.toml`, in USD per million tokens; `cached_input` and `cache_write` are optional and
fall back to `input`. An override beats the shipped price, and `provider/*` covers every model of a
provider (handy for a local server):

```toml
[pricing."openai/gpt-6-luna"]
input = 0.08
output = 0.40
cached_input = 0.008

[pricing."ollama/*"]
input = 0
output = 0
```

`config show` lists the price of every resolved model and any override.

### Budgets

Set any of `budget.max_cost_usd`, `max_tokens`, `max_wall_seconds`, `max_tool_calls` (unset means
unlimited). The run records a `budget.warning` event at 80% of a limit. When a limit is **passed** the
run is stopped cooperatively: tools answer "run cancelled" so agents wind down, and at the next safe
point (a stage boundary, or the end of the run) the run fails with `Budget exceeded: ...` and exit
code 1. `budget.max_repair_rounds` is separate: it caps fix-and-verify rounds and is asked by the
verification loop before each repair.

```toml
[budget]
max_cost_usd = 5.0
max_wall_seconds = 3600
max_tool_calls = 800
```

Budgets are a safety net, not a to-the-cent cap:

- Usage is read from each model call's event *after* the call finishes, so a call already in flight
  when a limit trips still completes and is billed. With several agents running at once the overrun
  can be one in-flight call per agent.
- The tool-call limit is exact for sequential work; concurrent tool calls can overshoot by a few.
- A cost limit needs a known price for every model used. With an unpriced model it cannot be
  enforced; the run says so (`budget.warning`, and a note in the end-of-run report). Add a price
  override or use a token budget instead.
