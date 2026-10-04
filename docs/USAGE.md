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
| `maintain --task T --repo PATH` | Maintain an existing project: `add-tests`, `refactor`, `upgrade-deps`, `docs`, `security-audit`, `custom`, or a recipe of your own ([Maintaining a project](#maintaining-a-project)). |
| `review [--base BRANCH]` | A read-only review of a branch or your working diff; `findings.json` for CI, exit 3 on a serious finding ([Reviewing a change](#reviewing-a-change)). |
| `recipes list` / `recipes show NAME` | Which recipes exist (bundled, yours) and what each stage does ([Your own recipes](#your-own-recipes)). |
| `diff [RUN] [--stat]` / `export-patch [RUN] --out FILE` | What a `feature` or `fix` run changed, and that change as a patch for `git apply`. |
| `analyze [--repo PATH] [--deep]` | Look at an existing project without changing it: languages, detected commands, tests, CI, Git state; `--deep` also writes a codebase map ([Adopting an existing project](#adopting-an-existing-project)). |
| `resume RUN` | Continue a cancelled, interrupted, or failed run without redoing finished stages. |
| `report [RUN] [--format html|md] [--open]` | Write the run report: summary, timeline, board, checks, cost, changes ([Run reports](#run-reports)). |
| `ui [--host H] [--port P]` | Serve the web UI's API on localhost ([Web UI](#web-ui)). Needs `uv sync --extra ui`. |
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

### Run reports

Every run ends by writing `report.html` in its run directory (`.engineering-team/runs/<run-id>/`); the
end-of-run summary prints its path (`run_report` in `--json`). Open it in any browser: it is one
self-contained file (no network, light and dark), so you can attach it to a ticket or keep it with the run.

```bash
uv run engineering-team report                    # (re)write the latest run's report.html
uv run engineering-team report 20261003 --open    # a run by id prefix, then open it
uv run engineering-team report --format md        # report.md, for a PR or a chat
```

It starts with a status banner; when the run did not succeed, the banner lists why (the failing stage and
its error, the failed required checks, the unmet budget, the questions for you). Below: warnings (budget,
checks that could not run, criteria no check proves, a bug that was not reproduced), the stage timeline
with parallel lanes, the final task board with each card's history, tool calls per teammate, screenshots
from the browser tools, usage and cost against the budget, the checks with log excerpts, the
criteria-coverage matrix (unproven criteria are flagged), review findings, a diff viewer with per-file
stats (for `feature`, `fix`, and `maintain` runs), and the versions and settings the run used.

The report is written by the controller from files in the run directory; text written by agents or taken
from your repository is shown escaped and is never used to decide a status. `report` works on a run that is
still going or that died, and rewrites the file each time.

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

**How the team gets a place to work.** The modes that change an existing project (`feature`, `fix` and `maintain`) start from the `adopt` recipe: profile, baseline, map. They never work in your checkout blindly;
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

### Maintaining a project

```bash
uv run engineering-team maintain --repo ../my-service --task add-tests --goal "the billing package"
uv run engineering-team maintain --repo ../my-service --task refactor --goal "split handlers.py by resource"
uv run engineering-team maintain --repo ../my-service --task upgrade-deps --sandbox docker
uv run engineering-team maintain --repo ../my-service --task docs
uv run engineering-team maintain --repo ../my-service --task security-audit            # report only
uv run engineering-team maintain --repo ../my-service --task security-audit --fix      # and fix the serious ones
uv run engineering-team maintain --repo ../my-service --task custom --goal "Replace print() with logging"
```

`maintain` works like [`feature`](#feature-mode): isolated on a branch, worktree, or copy (`--worktree`, `--allow-dirty`,
`--squash`), verified against the baseline, with `CHANGE_SUMMARY.md` and `changes.patch` in the run directory, and
`resume` continues a failed run. Each task is a recipe (`recipes show add-tests`) that adds **policies**: what the task
may change, measured by the controller from Git and recorded as a required check `policy:<name>` in the verification
(a violation is handed to the repair agent like a failing test: "revert these files"), and a **write scope** that makes
the agent's own file tools refuse a path outside its lane.

| Task | What it does | May change | Also |
|------|--------------|------------|------|
| `add-tests` | Characterization tests for untested code: they pin what the code does today, bugs included | test files only (`tests_only`) | The controller measures coverage before and after with the project's own coverage tool (coverage.py, jest/vitest, `go test -cover`, cargo-llvm-cov) and reports the delta in the summary; with no tool it says so. |
| `refactor` | A behaviour-preserving refactor of what `--goal` names | anything except tests and manifests (`tests_untouched`, `manifests_untouched`), at most `maintain.max_refactor_lines` lines (`diff_size`) | **Starts only if the project's tests pass**: with failing or no tests it stops, changing nothing, with exit code 4 and the next step (`fix`, or `add-tests` first). The tests run again afterwards and are compared with the baseline. |
| `upgrade-deps` | An analyst plans the upgrades (it asks the registries; it does not assume versions); the controller applies them in groups of `maintain.upgrade_group_size`, installs, and runs the project's checks after each group | manifests and lockfiles only (`manifests_only`) | A group that fails is undone and halved until the upgrade that broke the checks is found; the summary lists what went in, what could not and **why (the check that said so)**, and what the analyst left alone. It installs packages: use `--sandbox docker` for code you do not trust. |
| `docs` | The technical writer documents the legacy code | documentation files only (`docs_only`: Markdown, reStructuredText, `docs/`, README, CHANGELOG; not even comments) | |
| `security-audit` | The controller runs the ecosystem's dependency audit (pip-audit, npm audit, cargo-audit; needs `tools.dev.allow_network`), the security engineer reads the code, the controller merges both | nothing, unless `--fix`: then the debugger fixes the findings at `review.fail_on` or above and the controller verifies against the baseline | `findings.json` and `findings.md` in the run directory; without `--fix` the run exits 3 when a finding reaches `review.fail_on`. |
| `custom` | Whatever `--goal` (or `--goal-file`) says, with the smallest change, verified against the baseline | anything | The recipe to copy for your own ([below](#your-own-recipes)). |

### Reviewing a change

```bash
uv run engineering-team review --base main                       # everything on this branch since main
uv run engineering-team review                                    # your uncommitted changes, or the work since main/master
uv run engineering-team review --base origin/main --out-dir ci-artifacts --focus "the new migration"
```

`review` is read-only and runs in place: nothing in your project is written except the controller's own state
directory, which Git is told to ignore. `code_reviewer` and `security_engineer` read the diff against the merge base
of `--base` (side by side, as in the other modes' review stage), and the controller consolidates their findings
(numbers them, drops those without text or with a path outside the project, merges duplicates) and writes to the run
directory, and to `--out-dir`:

- `findings.json`: `kind`, `run_id`, `base`, `base_branch`, `head`, `fail_on`, `passed`, `counts` per severity, and the
  `findings` (`id`, `severity`, `summary`, `file`, `line`, `suggested_fix`, `source_role`), most severe first;
- `findings.md`: the same for people.

The exit code is the contract for a pipeline: **0** when no finding reaches `review.fail_on` (default `high`), **3**
when one does, **2** for a bad `--base` or a directory that is not a repository, 1 for a failure. A clean branch with
nothing to compare says so and exits 0. `--json` prints the findings on stdout. A finding is a claim to check, not a
proven defect.

### Your own recipes

A recipe is a YAML file: an ordered list of stages. Put yours in `.engineering-team/recipes/NAME.yaml` (the project) or
`~/.config/engineering-team/recipes/NAME.yaml` (every project); the project's wins over yours, and either wins over a
bundled recipe of the same name, so a file can also replace `fix` or `custom`. Run one with
`maintain --task NAME [--goal "..."]`. `recipes list` shows what exists and which file wins (a file that is wrong is
listed with its error); `recipes show NAME` prints its stages. The file's `name:` must be its file name, and a run keeps
a copy of the recipe it started with, so `resume` works from a worktree and after you edit the file.

```yaml
name: tidy                         # = tidy.yaml
description: Remove dead code and keep the tests as they are.
policies: [tests_untouched]        # enforced by the verify stage (see below)
stages:
  - {name: profile, kind: controller, action: repo_profile}
  - {name: baseline, kind: controller, action: baseline}
  - name: sweep
    kind: agent
    teammates: [backend_engineer]
    instructions: >                # the task, in your words (or `prompt:` to use one from config/stages.yaml)
      Remove code nothing calls (check with the reference tool) and change nothing else.
    write_scope: tests             # optional: the paths this agent's file tools may write
    retry: 1
  - {name: verify, kind: verify, teammates: [debugger]}
  - {name: summary, kind: controller, action: change_summary}
```

The catalogue a recipe is validated against (an error names the file, the stage, and what to fix, before anything runs):

| Part | Values |
|------|--------|
| `kind` | `controller` (runs an `action`, no model), `agent` (one teammate, one task; `outputs` names a contract), `parallel` (one agent per work package of the plan), `verify` (the controller's checks, with bounded repair by its first teammate), `review` (reviewers side by side, read-only), `analyze` (the codebase map), `reproduce` (fix mode's red gate), `upgrade` (upgrade-deps' grouped upgrades) |
| `action` | `repo_profile`, `baseline`, `change_summary`, `coverage_before`, `coverage_after`, `refactor_precondition`, `dependency_audit`, `security_report`, `security_gate`, `review_target`, `review_gate` |
| `teammates` | Keys from `team list` (including your own teammates) |
| `prompt` / `instructions` | A prompt key of `config/stages.yaml`, or your own text for the stage (not both) |
| `policies` (recipe) | `tests_only`, `docs_only`, `manifests_only`, `tests_untouched`, `manifests_untouched`, `diff_size`; they need a `verify` stage |
| `write_scope` | `tests`, `docs`, `manifests` (agent, verify and upgrade stages) |
| `skip_if` | `no_work_packages`, `minimal_team`, `fixes_requested`, `fixes_not_requested`, `no_findings`, `nothing_to_review` |
| `retry`, `optional`, `inputs`, `outputs`, `repair: false` (review), `allow_shared` (parallel) | as in [ARCHITECTURE.md](ARCHITECTURE.md#pipeline-recipes-and-resume) |

**`new --adopt`** is the narrow, older door: it lets `new` work in an existing directory under the workspace
root that the tool did not create (`--project-name NAME --adopt`), in place and without isolation, so it
refuses a Git repository (committing there would land on its current branch). The directory is marked
*adopted* (`.engineering-team/owner.json`): `--reset` refuses to delete it without `--force-reset`.
Safety details: [SAFETY.md](SAFETY.md#adopting-an-existing-project).

## Web UI

```bash
uv sync --extra ui                     # FastAPI and uvicorn (`doctor` says if they are missing)
uv run engineering-team ui             # http://127.0.0.1:8765/api/v1 ; interactive docs at /api/v1/docs
```

`ui` serves a JSON API over the runs of the workspace root (`--workspace-root`, `--config` as for every command): start
a run (`POST /runs`: JSON, or `multipart/form-data` with a `spec` field and `request_files` / `context_files`), list and
inspect runs, stream their events (Server-Sent Events, resumable with `Last-Event-ID`), cancel, resume, pause, steer a card
or the run, answer the team's questions, read the board (now, or replayed at any event with `?at=SEQ`), teammates,
cards with their tool-call trail, artifacts, project files, the diff, and the run report, plus `GET /config`, `/doctor`,
`/team`, `/recipes`, and `/repo/inspect?path=`. The endpoint reference is in
[ARCHITECTURE.md](ARCHITECTURE.md#web-ui-backend).

```bash
H='X-Engineering-Team: 1'              # required on every POST
curl -s -H "$H" -X POST localhost:8765/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"mode": "new", "request": "Build a CLI that stores and lists notes.", "options": {"profile": "smoke"}}'
curl -N localhost:8765/api/v1/runs/RUN_ID/events                       # live events
curl -s -H "$H" -X POST localhost:8765/api/v1/runs/RUN_ID/cancel       # then .../resume
```

**Runs are separate processes** (`engineering-team ... --run-id`), so a run keeps going if you stop the server, and `status`,
`board`, `note`, and `cancel` work on a run the UI started. At most `ui.max_concurrent_runs` (default 2) are kept going;
the workspace lock lets one write to a project at a time. A start's options are the ones the CLI has (`strategy`,
`profile`, `provider`, `sandbox`, `allow_web`, the isolation flags of `feature`/`fix`/`maintain`) plus `budget`,
`max_parallel_agents`, and `disabled_teammates`; a request that the settings would refuse is a 422 before anything starts.
When a run's team asks a question, `GET /runs/{id}/questions` lists it and `POST /runs/{id}/answer` replies (an empty reply
lets the team assume; start with `"interactive": false` to never be asked).

**Security.** The server listens on `127.0.0.1` and answers only requests addressed to `localhost`, `127.0.0.1` or
`[::1]`; a request from another origin is refused, no CORS headers are sent, and POST calls need the header
`X-Engineering-Team: 1`, so a web page you visit cannot drive it. To listen on another address you must pass
`--host ADDRESS --allow-remote`; the server then requires `Authorization: Bearer <token>` (a new random token, printed
at start; use TLS or an SSH tunnel, the server speaks plain HTTP). Anyone with the token can start runs that use your
model credentials and read the files of any project you can name, so treat it like a password. Never served: `.env`,
private keys and other credential-looking files, `.git`, and the controller's state directory; paths cannot leave the
project (symlinks included), uploads are text files with a size cap, and request bodies are capped
([CONFIGURATION.md](CONFIGURATION.md#web-ui-ui)).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (a `pipeline` run: verified). |
| `1` | A runtime error, or a failed check of your setup (`doctor`). |
| `2` | Usage or configuration error: one line on stderr, no traceback. |
| `3` | The controller's own checks failed (not verified); for `review` and an unfixed `security-audit`, a finding reached `review.fail_on`. |
| `4` | Verification was partial (a required check could not run), or `fix` or `maintain --task refactor` stopped because it needs more information (verdict `needs-info`, with questions). |
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
