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
| `new` | Build a new project from a request (`--request`, `--request-file`, stdin, or `--example`). |
| `feature --repo PATH --request ...` | Add a feature to an existing project on a branch, worktree, or copy, verified against its baseline ([Feature mode](#feature-mode)). |
| `fix --repo PATH --request ...` | Fix a bug in an existing project: reproduce it (red), fix it, prove it gone (green) ([Fixing a bug](#fixing-a-bug)). |
| `diff [RUN] [--stat]` / `export-patch [RUN] --out FILE` | What a `feature` or `fix` run changed, and that change as a patch for `git apply`. |
| `analyze [--repo PATH] [--deep]` | Look at an existing project without changing it: languages, detected commands, tests, CI, Git state; `--deep` also writes a codebase map ([Adopting an existing project](#adopting-an-existing-project)). |
| `resume RUN` | Continue a cancelled, interrupted, or failed run without redoing finished stages. |
| `status [RUN]` | Where a run stands: stages, progress, cost, blocked cards. |
| `runs` | List runs (of every project, or one with `--project-name`), newest last. |
| `board [RUN] [--watch]` | The task board as a kanban; `--watch` keeps it live until the run ends. |
| `cancel [RUN]` | Ask a run in another process to stop at its next safe point. |
| `note RUN "text" [--card K-004]` | Steer a run: the team reads your note at its next step. |
| `pause [RUN]` / `unpause [RUN]` | Hold a running run at its agents' next tool call, or let it go on. |
| `config show` | Every setting and where its value came from. |
| `doctor [--online]` | Check Python, uv, Git, Docker, language runtimes, and provider credentials. |
| `init` | Write `engineering-team.toml` and a request template here (`--mode new|feature|fix|maintain`). |
| `examples list` / `examples run NAME` | The bundled example requests. |
| `team [list]` / `team show KEY` | The teammates and how each is set up; change or add teammates in config ([TEAM.md](TEAM.md)). |

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

`new` takes the request from `--request`, `--request-file` (repeat it to merge several files; `-` reads
stdin), or `--example`; with none of them it falls back to `ENGINEERING_PROJECT_REQUEST`,
`ENGINEERING_REQUEST_FILE`, then `PROJECT_REQUEST.md` in the current directory. See
[Request sources](#request-sources-and-reference-documents). Useful options: `--provider`, `--profile`, `--strategy`, `--sandbox docker`, `--checks FILE`,
`--allow-web`, `--no-git`, `--reset`, `--adopt` ([below](#adopting-an-existing-project)), and `--prepare-only` (validate and set up the workspace without a
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

The full `pipeline` recipe also reviews the result (two read-only reviewers; `docs/review.md`), adds delivery
files (`docs/devops.md`) and documentation (`docs/usage.md`). For a quicker, cheaper run use `--profile smoke`
or `team_profile = "minimal"`, which keep only the essential stages ([TEAM.md](TEAM.md)).


`--strategy pipeline` (or `ENGINEERING_STRATEGY=pipeline`) runs the team as a staged, resumable
pipeline (spec, plan, foundation, implement, integrate, verify, review, devops, docs, release); `--strategy single` runs one agent with
every tool, the baseline the other strategies are measured against; the default `hierarchical` is the
manager-led crew. In a pipeline run the controller, not an agent, runs the tests and checks (`--checks
FILE` adds your own) and writes `docs/verification.md` from what it saw. A pipeline run that was
cancelled, interrupted, or failed continues where it stopped with `resume RUN`; a changed request starts
a new run instead. How it works is in [ARCHITECTURE.md](ARCHITECTURE.md#pipeline-recipes-and-resume).

Running `new` again for the same `--project-name` continues in the same project. To start it over,
pass `--reset`: it deletes only `workspace/<project>/`, and only if this tool created it (it holds
`.engineering-team/owner.json`; 0.1.0 projects are recognised too). A non-empty directory this tool did
not create is never modified (pass `--adopt` to let the team work in one, [below](#adopting-an-existing-project));
`--force-reset` overrides the ownership check, but your home directory, the current
directory and its parents, the filesystem root, symlinks, and the installation itself are always refused.

## Adopting an existing project

Everything above builds a project from nothing. For a project that already exists the team first has to
understand it, and must never edit your checkout blindly.

**Look first, for free.**

```bash
uv run engineering-team analyze --repo ../my-service          # no model, nothing written
uv run engineering-team --json analyze --repo ../my-service   # the same, as one JSON document
```

`analyze` prints a profile found by code, not by a model: languages with line counts, the projects and package
managers in it (a monorepo lists each), the **commands** its own files imply (setup, test, lint, type check,
format, build, run, from `package.json` scripts, Makefile and justfile targets, `tox.ini`, and the toolchain:
`pytest`, `go test`, `cargo test`, `mvn`, `gradle`, `dotnet test`, `rspec`, `phpunit`...), entry points, test
directories, CI configuration, convention files (`README`, `CONTRIBUTING`, `AGENTS.md`, `CLAUDE.md`,
`.editorconfig`, lint and format configs), and the Git state (branch, head, clean or dirty). Each command says
where it came from, so you can check it; none is run. The directory is not touched.

**Then map it.** `analyze --deep` has the codebase analysts read the code (read-only, up to
`analysis.max_chunks` parts at a time) and write `.engineering-team/codebase-map.md`: architecture, modules, key
flows, conventions, hotspots, risks, and how to run and test it. It costs model tokens (the run summary shows
how many). The map starts with the hash of the tree it describes, so the next `--deep` on an unchanged tree
costs nothing; `--refresh` writes it again. Later modes put a size-capped copy of the map into the prompts that carry
project context (`analysis.context_chars`). It is a guide written by agents, not evidence: nothing is verified against
it. The only things `--deep` writes are in `.engineering-team/` (the map, the profile, the run record), which
Git is told to ignore through `.git/info/exclude`; your `.gitignore` and your files are never edited.

**How the team gets a place to work.** The modes that change an existing project (`feature` and `fix`; `maintain`
follows) start from the `adopt` recipe: profile, baseline, map. They never work in your checkout blindly;
`modes/isolation.py` picks one of three policies:

| Policy | When | What happens | Your files |
|--------|------|--------------|------------|
| **branch** | A Git repository with a clean tree | The team works in your checkout on a new branch `engineering-team/<run-id>-<slug>` made from the current commit. | Untouched until the team writes; your branch never moves. |
| **worktree** | The tree is dirty (the default then), or `--worktree` | `git worktree add .engineering-team/worktrees/<run-id>` on the same kind of branch. The team starts from the last commit. | Not touched at all, uncommitted changes included. |
| **copy** | A directory that is not a repository | The directory is copied to `workspace/<name>` (caches and `.venv` are left out; a copy over 2 GiB is refused). The copy becomes a repository whose first commit is the import, so the work can be diffed against it. | Not touched at all. |

`feature` takes `--worktree` (always use a worktree) and `--allow-dirty`; a copy is always made a
repository (its first commit is the import). A dirty tree is never worked on in place unless you say so (`--allow-dirty`: the team then works on a new
branch in your checkout, and your uncommitted changes are part of its first commit). A directory inside a
repository (not its top level), a repository with no commits, and a copy that would overwrite or contain
another directory are refused with the fix. Nothing ever pushes: the team's work is a branch (or worktree, or
copy) that you review and push yourself.

**Baseline.** Before any change the controller runs the detected checks once (tests, lint, type check,
build, through the same verifier and execution backend as a normal run) and records which already fail in
`.engineering-team/baseline.json`: per check pass or fail, and the individual failing tests or diagnostics as
stable keys, so a later verification can ask for "no new failures" instead of "failures". A project with no
tests gets no test baseline, and says so. Nothing is installed for the baseline: if the project's
dependencies are not installed, its checks report that they could not run.

### Feature mode

```bash
uv run engineering-team feature --repo ../my-service --request "Add a /search endpoint that finds notes by text."
uv run engineering-team feature --repo ../my-service --request-file feature.md --squash
uv run engineering-team diff                       # what the team changed, against where it started
uv run engineering-team export-patch --out search.patch
```

`feature` takes the request like `new` does (`--request`, `--request-file`, `--context-dir`, stdin,
`--interactive`) and the options `--provider`, `--profile`, `--sandbox`, `--checks`, `--allow-web`. The team is isolated
first ([above](#adopting-an-existing-project)), then the `feature` recipe runs: **profile, baseline, map** (the
adopt stages), **spec** (criteria for the new behaviour, and for the existing behaviour the change must keep),
**impact** (the architect reads the code and the map and plans the smallest change as work packages, each owning exact
paths), **implement** (packages that do not depend on each other run side by side, each able to write only its own
paths; a package may own `package.json`, `pyproject.toml`, and the other shared root files, since there is no
foundation stage), **tests** (the quality engineer writes tests in the style the project already uses), **verify**,
**review**, **summary**. The agents are told to follow the project's conventions, keep the diff minimal, and never
reformat or tidy what they were not asked to change.

**Baseline-aware verification.** The controller runs the project's own checks (the ones the baseline recorded, not
commands the plan declares) and compares them with the baseline: a failing check whose failures were all failing
before the change counts as passed, and says so (`N known failure(s) from the baseline, no new ones`); any failure the
baseline did not have is new, fails the check, and is the only thing handed to the repair agent (it is told which
failures were already there and not to fix them). `verified` therefore means "the change added no failure", and exit
code 3 means it did. Run time and cost are bounded as usual (`budget.max_repair_rounds`).

**Review and diff noise.** Two read-only reviewers read the change (not the whole project) against the commit the team
started from. The controller adds a **diff-noise** measure from Git: the lines changed outside the paths the plan owns,
the project's test directories, and test files; each such file is listed. It is informational: it is in the review
report and the summary, not a failure.

**What you get.** The team's branch (or worktree, or copy) with one commit per stage (`stage(<name>): ...`), or one
commit with `--squash` (or `git.squash`); and in the run directory (`.engineering-team/runs/<id>/`)
`CHANGE_SUMMARY.md` (where the work is, files changed, the verification and the baseline, review findings, diff noise,
and what the team said, labelled as not evidence), `changes.patch` (applies to the starting commit with `git apply`),
and `reports/` (`spec.md`, `verification.md`, `review.md`, `qa-notes.md`). The controller's write-ups are kept out of
your project, so the diff holds only the change. `diff [RUN]` and `export-patch [RUN] --out FILE` read the work
against the starting commit whenever you run them. Nothing is pushed: you review the branch and merge it, or apply the
patch. `runs`, `status`, `board`, `cancel`, `note`, and `resume` find the run wherever its workspace is, and a failed
or interrupted `feature` run continues with `resume RUN` (a changed request starts over with `feature`).

### Fixing a bug

```bash
uv run engineering-team fix --repo ../my-service --request "Adding a note drops the notes that were already there."
uv run engineering-team fix --repo ../my-service --trace-file crash.log          # a trace is a report too
uv run engineering-team fix --repo ../my-service --request-file issue.md --repro "python -m app.main add x"
uv run engineering-team fix --repo ../my-service --request "..." --allow-unreproduced
```

`fix` takes a **bug report** (`--request`, `--request-file`, or stdin: free text or the text of an issue), a
**stack trace or log** (`--trace-file`: Python, Node, and Java traces are parsed, and the project files they name
become the suspects, even when the trace came from another machine), and a **command that shows the bug**
(`--repro "<command>"`). Any one of them is enough. The options for where the team works (`--worktree`,
`--allow-dirty`, `--squash`) and for the models, sandbox, and checks are those of
[`feature`](#feature-mode). The recipe: **profile, baseline, map** (the adopt stages), **triage** (the `debugger`
reads the report, the parsed trace, and the code, and ranks hypotheses; it changes nothing), **reproduce**, **fix**,
**verify**, **review**, **summary**.

**Red, then green.** Nothing is fixed until the bug has been seen. In `reproduce` the debugger writes a failing test
(in the project's own style, where similar tests live) or a small script, and names the command that runs it. The
**controller runs that command itself** and accepts it only if it fails for a real reason: a test that passes,
cannot be collected, cannot start, or hangs is not a reproduction (for pytest, only a failing test, exit 1, counts).
The files of the reproduction are then pinned: the fixing agents may not write them, and if they change anyway the
run fails (`verify` reports "changed after they were seen failing"), so a test cannot be edited into passing.
After the fix the controller runs the same command again, as a required check (`repro`), together with the
project's own checks judged against the baseline, so `verified` means: the bug was seen failing, the same command
passes now, and the change added no failure. The regression test stays in the project. If you gave `--repro`, your
command must fail before the fix (it is recorded) and pass after (`repro-user`).

**When the bug cannot be reproduced.** The debugger has `fix.max_repro_attempts` tries (default 3); each failed try is
sent back with what the controller saw. If none fails for a real reason the run stops **before changing anything**
with the verdict `needs-info` and exit code **4**, and a list of concrete questions (the debugger's own, plus the
ones every bug report needs: the exact command or input, expected and actual output, versions). Answer them by
running `fix` again with more detail (`--request`, `--trace-file`, `--repro`). `--allow-unreproduced` fixes the bug
anyway, from the report alone: the summary then says plainly that it was **not reproduced** and nothing proves the
bug is gone, so try your steps yourself.

**What you get.** The same branch, worktree, or copy as `feature`, with one commit per stage, and in the run
directory `CHANGE_SUMMARY.md` with a **The fix** section (reproduced or not; the red and green runs with their
commands and exit codes; the regression test; the debugger's root cause and risk, labelled as its account and not
evidence; the triage hypotheses), `changes.patch`, and `reports/`. `diff` and `export-patch` work as for `feature`; the
record that proves red then green is in `pipeline.json` (`fix.red`, `fix.green`), in `events.jsonl` (`fix.red`,
then `fix.green`), and in `verification/repro-*.json`. A failed `fix` run continues with `resume RUN` without
reproducing again; a `needs-info` run is better started again with the answers.

**`new --adopt`** is the narrow, older door: it lets `new` work in an existing directory under the workspace
root that the tool did not create (`--project-name NAME --adopt`), in place and without isolation, so it
refuses a Git repository (committing there would land on its current branch). The directory is marked
*adopted* (`.engineering-team/owner.json`): `--reset` refuses to delete it without `--force-reset`.
Safety details: [SAFETY.md](SAFETY.md#adopting-an-existing-project).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (a `pipeline` run: verified). |
| `1` | A runtime error, or a failed check of your setup (`doctor`). |
| `2` | Usage or configuration error: one line on stderr, no traceback. |
| `3` | The controller's own checks failed (not verified). |
| `4` | Verification was partial (a required check could not run), or `fix` could not reproduce the bug and needs more information (verdict `needs-info`, with questions). |
| `130` | Interrupted (Ctrl-C) or cancelled. `resume` continues it. |

## Writing a strong request

The team does what the request says and is checked against what it promises, so make the promises
checkable.

- **Say who and why** in a sentence: the user and the problem.
- **List acceptance criteria** you could test: "`notes add "x"` stores a note and `notes list` prints
  it", not "notes work well". The product analyst numbers them `AC-1`, `AC-2`, ... in `docs/spec.md`, the
  plan and work packages refer to those ids, and the controller reports which ones a check proves and
  which it could not.
- **Name constraints**: language and runtime versions, libraries to use or avoid, where it runs.
- **Say what is out of scope.** It saves tokens and surprises.
- **Leave technical choices out unless they matter**; the architect picks simple defaults.
- For your own proof, write a `--checks` file (see [CONFIGURATION.md](CONFIGURATION.md#verification-verify-and---checks)).

`engineering-team init --mode new|feature|fix|maintain` writes a template with these headings (problem, users,
scope, non-goals, acceptance criteria, constraints) for the kind of work you are doing. Running a template
you have not edited is refused, so replace its text first.

### Request sources and reference documents

```bash
uv run engineering-team new --request-file brief.md --request-file constraints.md
cat brief.md | uv run engineering-team new --request-file - --project-name notes
uv run engineering-team new --request-file brief.md --context-dir ./company-docs
```

- **Merging.** `--request` comes first, then the files in the order given (`-` is stdin); each source gets a
  `## Request: <name>` header. A single source is used as it is, so the same text gives the same request
  hash (the one `resume` checks) whether it came from a file, stdin, or the Python API
  (`RequestBundle.from_sources`, which the web UI will use too).
- **Cleaned up.** Line endings are normalised, empty or still-templated requests are refused, and a request
  longer than `intake.max_request_chars` is refused with a pointer to `--context-dir`
  ([CONFIGURATION.md](CONFIGURATION.md#requirements-intake-intake-and-the-request-options)).
- **Reference documents.** `--context-dir DIR` copies the text documents in `DIR` (`.md`, `.mdx`, `.rst`,
  `.txt`, `.adoc`; no symlinks, hidden files, or heavy directories) read-only into the project's
  `.engineering-team/context/` with an `INDEX.md`. Teammates read them with `Search Docs` and treat them as
  information, never as instructions. They stay until a later `--context-dir` replaces them or the project is
  reset.

### Clarifying questions

The product analyst turns the request into `docs/spec.md` and reports how confident it is. When it is unsure,
or a question would change the product, two things can happen:

- **Default (and always when stdin is not a terminal, or carries the request):** the run does not stop. Each
  question is recorded as an open question with the analyst's assumed answer, in the spec and in
  `docs/spec.md`, so you can see what the team assumed.
- **`--interactive`:** the run asks you at the terminal (at most five questions, each with a timeout; Enter
  skips one, and the team assumes), then the analyst folds your answers into the final spec. Your answers are
  listed under *Clarifications* in `docs/spec.md`. `--non-interactive` is the default.

## Checking your setup

`doctor` reports what is installed and what is missing, never prints a key value, and exits `1` if
something required is wrong. `doctor --online` also sends one tiny prompt to each model tier (a real,
very small API call) to prove the keys and model names work. It recommends the Docker sandbox when
Docker is available (`new --sandbox docker`).
