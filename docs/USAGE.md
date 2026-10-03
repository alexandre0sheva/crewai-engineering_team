# Usage

The command-line reference, the workflows, and how to write a request the team can succeed with.
Settings are in [CONFIGURATION.md](CONFIGURATION.md), the safety model in [SAFETY.md](SAFETY.md).

```bash
uv run engineering-team --help           # every command
uv run engineering-team <command> --help # its options
```

## Commands

| Command | What it does |
|---------|--------------|
| `new` | Build a new project from a request (`--request`, `--request-file`, or `--example`). |
| `resume RUN` | Continue a cancelled, interrupted, or failed run without redoing finished stages. |
| `status [RUN]` | Where a run stands: stages, progress, cost, blocked cards. |
| `runs` | List runs (of every project, or one with `--project-name`), newest last. |
| `board [RUN] [--watch]` | The task board as a kanban; `--watch` keeps it live until the run ends. |
| `cancel [RUN]` | Ask a run in another process to stop at its next safe point. |
| `note RUN "text" [--card K-004]` | Steer a run: the team reads your note at its next step. |
| `pause [RUN]` / `unpause [RUN]` | Hold a running run at its agents' next tool call, or let it go on. |
| `config show` | Every setting and where its value came from. |
| `doctor [--online]` | Check Python, uv, Git, Docker, language runtimes, and provider credentials. |
| `init` | Write `engineering-team.toml` and a request template here. |
| `examples list` / `examples run NAME` | The bundled example requests. |
| `team` | The teammates (a configurable registry arrives in a later release). |

`RUN` is a run id or enough of its start to be unambiguous; leave it out for the latest run. Runs are
found across every project under the workspace root, so `--project-name` is only needed to narrow the
search.

**Global options** work before or after the command: `--json`, `--quiet`/`-q`, `--verbose`/`-v`,
`--no-color`, `--workspace-root DIR`, and `--version`.

- `--json` makes stdout one JSON document (and only that); logs go to stderr. For `new` and `resume`
  it is the final result: `run_id`, `status`, `verdict`, `exit_code`, `usage`, `files_changed`,
  `stages`, `checks`, `workspace`, `report`, `next_steps`.
- `--quiet` and any output that is not a terminal print timestamped log lines instead of the live
  view; `--quiet` keeps only warnings and the result.
- `-v` shows CrewAI's own console output, which is off by default because it fights the live view.
  (It overrides the `verbose` setting; with the CLI you control it with the flag.)
- `NO_COLOR` is honoured as well as `--no-color`.

## Starting a run

```bash
uv run engineering-team new --example tiny-notes          # a cheap bundled example
uv run engineering-team new --request-file habit-tracker.md --project-name habit-tracker
uv run engineering-team new --request "Build a CLI that stores and lists notes." --prepare-only
```

`new` takes the request from the first of `--request`, `--example`, `--request-file`,
`ENGINEERING_PROJECT_REQUEST`, `ENGINEERING_REQUEST_FILE`, then `PROJECT_REQUEST.md` in the current
directory. Useful options: `--provider`, `--profile`, `--strategy`, `--sandbox docker`, `--checks FILE`,
`--allow-web`, `--no-git`, `--reset`, and `--prepare-only` (validate and set up the workspace without a
model call).

**The 0.1.0 form still works.** `engineering-team --request-file FILE` runs `new` and prints a deprecation
notice; it is removed in 0.3.0. The `crewai run|train|replay|test` entry points are unchanged, except that
`replay` names its project explicitly: `replay TASK_ID --run RUN_ID` (or `--project-name NAME`) instead of
guessing it from the environment.

### What you see

In a terminal, a live view: a header with the run, an overall progress bar, elapsed time, tokens, cost
and budget standing; the **kanban** (Backlog · Ready · In progress · Verifying · Blocked · Done, each
card with its id, title, assignee and age, blocked cards with the reason); one row per parallel **lane**
with its latest tool call; and a rolling activity feed. A narrow terminal gets a compact list instead of
columns. At the end, a summary: status, duration, usage and cost (or `unknown` when a model has no
price), files changed, the controller's checks, the workspace, the report, and what to run next.

The board is only filled in by the `pipeline` strategy; the default `hierarchical` strategy shows its
stages and activity but an empty board.

### Steering a run

From another terminal:

```bash
uv run engineering-team board --watch              # watch the kanban of the latest run
uv run engineering-team note 20261003 "Use SQLite, not files."
uv run engineering-team note 20261003 "Skip the export" --card K-004   # to one card's assignee
uv run engineering-team pause     # and `unpause`; agents stop at their next tool call
uv run engineering-team cancel
```

A note is delivered once to each teammate's next prompt. These commands reach the running process
through a small `inbox/` directory in the run (the same idea as the `cancel` flag), so they work from
any terminal; a note for a run that is not running waits until it is resumed.

### Strategies, resume, and starting over

`--strategy pipeline` (or `ENGINEERING_STRATEGY=pipeline`) runs the team as a staged, resumable
pipeline (spec, plan, foundation, implement, verify, release); `--strategy single` runs one agent with
every tool, the baseline the other strategies are measured against; the default `hierarchical` is the
manager-led crew. In a pipeline run the controller, not an agent, runs the tests and checks (`--checks
FILE` adds your own) and writes `docs/verification.md` from what it saw. A pipeline run that was
cancelled, interrupted, or failed continues where it stopped with `resume RUN`; a changed request starts
a new run instead. How it works is in [ARCHITECTURE.md](ARCHITECTURE.md#pipeline-recipes-and-resume).

Running `new` again for the same `--project-name` continues in the same project. To start it over,
pass `--reset`: it deletes only `workspace/<project>/`, and only if this tool created it (it holds
`.engineering-team/owner.json`; 0.1.0 projects are recognised too). A non-empty directory this tool did
not create is never modified; `--force-reset` overrides that, but your home directory, the current
directory and its parents, the filesystem root, symlinks, and the installation itself are always refused.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (a `pipeline` run: verified). |
| `1` | A runtime error, or a failed check of your setup (`doctor`). |
| `2` | Usage or configuration error: one line on stderr, no traceback. |
| `3` | The controller's own checks failed (not verified). |
| `4` | Verification was partial: a required check could not run. |
| `130` | Interrupted (Ctrl-C) or cancelled. `resume` continues it. |

## Writing a strong request

The team does what the request says and is checked against what it promises, so make the promises
checkable.

- **Say who and why** in a sentence: the user and the problem.
- **List acceptance criteria** you could test: "`notes add "x"` stores a note and `notes list` prints
  it", not "notes work well". The architect numbers them and the controller reports which ones a check
  proves and which it could not.
- **Name constraints**: language and runtime versions, libraries to use or avoid, where it runs.
- **Say what is out of scope.** It saves tokens and surprises.
- **Leave technical choices out unless they matter**; the architect picks simple defaults.
- For your own proof, write a `--checks` file (see [CONFIGURATION.md](CONFIGURATION.md#verification-verify-and---checks)).

`engineering-team init` writes a template with these headings.

## Checking your setup

`doctor` reports what is installed and what is missing, never prints a key value, and exits `1` if
something required is wrong. `doctor --online` also sends one tiny prompt to each model tier (a real,
very small API call) to prove the keys and model names work. It recommends the Docker sandbox when
Docker is available (`new --sandbox docker`).
