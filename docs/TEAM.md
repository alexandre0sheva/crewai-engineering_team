# The team

Who works on your project, what each teammate can use, and how to change or add teammates without
writing Python. The tools themselves are described in [TOOLS.md](TOOLS.md); the settings in
[CONFIGURATION.md](CONFIGURATION.md#team-team-and-team_file).

```bash
engineering-team team              # the roster (same as `team list`)
engineering-team team show product_analyst
```

## The roster

| Teammate | Does | Tier | Tool groups |
|---|---|---|---|
| `engineering_lead` | Manages the `hierarchical` strategy's crew: delegates and validates. Never used by `pipeline` or `single`. | `lead` | none (CrewAI gives it delegation tools) |
| `product_analyst` | Turns the request into the specification (`spec` stage). Read-only: the controller writes `docs/spec.md`. | `worker` | `fs_read`, `search`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `web`, `mcp:docs` |
| `solution_architect` | Designs the solution and splits the work (`plan` stage); writes `docs/architecture.md`. | `reviewer` (measured: the cheap `worker` model broke the plan rules in 3 of 11 pipeline runs, see [BENCHMARKS.md](BENCHMARKS.md#results-2026-10-04)) | read tools as above, plus `fs_write` |
| `backend_engineer` | Foundation, backend work packages, integration. | `worker` | `fs_read`, `fs_write`, `search`, `command`, `dev`, `runtime`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `web`, `mcp:docs` |
| `frontend_engineer` | Frontend work packages. | `worker` | as `backend_engineer`, plus `browser` |
| `quality_engineer` | Writes tests and the release report (`release` stage). | `worker` | as `frontend_engineer` (it needs `browser` to look at the UI) |
| `code_reviewer` | Read-only review of the finished project: correctness, maintainability, tests, diff noise (`review` stage). | `reviewer` | `fs_read`, `search`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `mcp:docs` |
| `security_engineer` | Read-only security review: secrets, injection, access control, unsafe defaults, and `Dependency Audit` (pip-audit, npm audit, cargo audit, only where installed and allowed). | `reviewer` | as `code_reviewer`, plus `dev` (the audit is the only `dev` tool a read-only reviewer gets) |
| `devops_engineer` | Dockerfile, CI workflow, `.env.example`, run scripts; records them in `docs/devops.md` (`devops` stage). | `worker` | `fs_read`, `fs_write`, `search`, `command`, `dev`, `runtime`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `web`, `mcp:docs` |
| `technical_writer` | README, `docs/usage.md`, and a changelog for the generated project (`docs` stage); changes documentation only. | `worker` | `fs_read`, `fs_write`, `search`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `mcp:docs` |
| `debugger` | Reproduces a failure, finds the root cause, makes the smallest fix: the repair agent of the `verify` stage, for failing checks and for review findings. | `worker` | everything but `web`: as `quality_engineer` without `web` |
| `codebase_analyst` | Reads an existing codebase and explains it: modules, key flows, conventions, risks, how to run and test (the `map` stage of the `adopt` recipe; `engineering-team analyze --deep`). Read-only: the controller writes `.engineering-team/codebase-map.md`. | `worker` | `fs_read`, `search`, `code_intel`, `git_read`, `board`, `notes`, `knowledge` (no `human`, no `web`) |
| `generalist_engineer` | Does everything alone (`single` strategy) and stands in for a missing teammate. | `worker` | as `frontend_engineer` |

Groups marked `web` and `browser` are used only when they exist: `web` needs `web.enabled` (and the
teammate in `web.roles`, if set), `browser` needs the optional Playwright extra. A group that is not
available is left out of the run silently; `engineering-team doctor` says what is missing.

Two choices differ from a strict "least privilege" table, because the pipeline's stages need them:
the architect keeps `fs_write` (it writes the architecture document) and the quality engineer keeps
`fs_write` (tests and the release report). Everyone has the coordination groups (`board`, `notes`,
`human`) and `knowledge` (Search Docs, which is also how reference documents from `--context-dir` are
read).

## Review and the optional stages

After the project is verified, the `new` recipe runs the **review** stage: the code reviewer and the
security engineer read the project side by side (each in its own parallel lane, read-only) and each returns a
list of findings. The controller checks them (severity, a real path inside the project), merges duplicates
(same file, nearby lines, similar words: one finding at the higher severity, naming both reviewers), numbers them
`F-1`, `F-2`, ..., and writes `docs/review.md`. Findings at or above `review.fail_on` (default `high`) go to the
`debugger` for one repair round (counted in `budget.max_repair_rounds`) and the project is verified again. The
`devops` and `docs` stages follow, then the release, and the final re-verify covers whatever they changed.

The three are optional. They are skipped by `--profile smoke`, by `team_profile = "minimal"`, and when none of
their teammates is enabled (`[team.devops_engineer] enabled = false`); with only one reviewer enabled, the other
simply does not run. See [CONFIGURATION.md](CONFIGURATION.md#review-review-and-team_profile).

## Tool groups

Every group, and what is in it, is listed in [TOOLS.md](TOOLS.md): `fs_read`, `search`, `fs_write`,
`command`, `dev`, `code_intel`, `git_read`, `knowledge`, `web`, `browser`, `runtime`, `board`, `notes`,
`human`. `mcp:docs` is not a tool group: it attaches the documentation MCP servers from
`docs_mcp_urls` (off in the `smoke` profile). Anything else is rejected with the list of known groups.

## Changing a teammate, or adding one

Teammates are defined in `config/agents.yaml` (the single source of the built-in prompts). Your
changes are layered on top of it, field by field, in this order; the last one wins:

1. the built-in definition,
2. `.engineering-team/team.yaml` in the directory you run the command from (or the file named by
   `team_file`),
3. `[team.<key>]` tables in `engineering-team.toml` (or any config file layer, see
   [CONFIGURATION.md](CONFIGURATION.md#where-settings-come-from)).

A key that is not built in adds a new teammate. `team show KEY` says which layers defined a teammate.

| Field | Meaning |
|---|---|
| `role`, `goal`, `backstory` | The prompt. `{project_name}` is filled in per run. A new teammate needs all three. |
| `tier` | `lead`, `worker` (default), `cheap`, or `reviewer`: which model tier it uses under the `standard` profile. `smoke` and `max-quality` fix the models of their two slots, so a cheap run stays cheap. To pin an exact model use `[models.roles.<key>]` (see [CONFIGURATION.md](CONFIGURATION.md#models)). |
| `tool_groups` | The groups above, plus `mcp:docs`. Default for a new teammate: read-only (`fs_read`, `search`, `code_intel`, `git_read`, `knowledge`, `board`, `notes`, `human`). |
| `allow_delegation` | Used by the `hierarchical` strategy's manager. Ignored by the pipeline (a stage runs one agent). |
| `max_iter` | Most reasoning steps per task (default: from the profile). |
| `enabled` | `false` takes a teammate out; see "Who steps in" below. |
| `modes` | Modes it works in (`new`, `feature`, `fix`, `maintain`); empty means all. |
| `stages` | Stages (such as `implement`) it may be given work packages for, in addition to the ones the recipe names. This is how a new teammate gets work. |

### Example: a data engineer, with no Python

`.engineering-team/team.yaml`:

```yaml
data_engineer:
  role: Data engineer for {project_name}
  goal: Design schemas and build the data pipelines the architecture asks for.
  backstory: You model data carefully, write migrations that can be rolled back, and test with real rows.
  tool_groups: [fs_read, fs_write, search, command, dev, board, notes]
  stages: [implement]
  tier: reviewer
```

or the same in `engineering-team.toml`:

```toml
[team.data_engineer]
role = "Data engineer for {project_name}"
goal = "Design schemas and build the data pipelines the architecture asks for."
backstory = "You model data carefully and test with real rows."
tool_groups = ["fs_read", "fs_write", "search", "command", "dev", "board", "notes"]
stages = ["implement"]
```

The architect is told who is available (the plan prompt lists each teammate and its role), so it can
give a work package the role `data_engineer`. Check it with `engineering-team team show data_engineer`.

Other changes work the same way: `[team.product_analyst] enabled = false`,
`[team.quality_engineer] goal = "..."`, `[team.backend_engineer] tier = "reviewer"`.

## Who steps in when a teammate cannot

A stage names its teammates (the `new` recipe's `spec` is the `product_analyst`'s, and so on). When the
first one is disabled, does not exist, or does not work in the run's mode, the next one listed works
it; failing that, the `generalist_engineer`; failing that, the enabled teammate with the tools most
like the missing one's. The run records a `team.fallback` event. A work package whose role names nobody
(or somebody disabled) goes the same way. If no enabled teammate can write files and run commands,
the run stops before starting with a message saying which teammate to enable.

## Safety

A teammate can only use the groups it is given, and the write scope, protected paths, and command
allowlist apply to everyone ([SAFETY.md](SAFETY.md)). Agents cannot read or change the team files
(`.engineering-team/` in the project workspace is closed to them; the team file lives in your working
directory). The `hierarchical` strategy has a fixed shape: four specialists and a manager, with the
project tools only. It uses the roster for their prompts, tiers, and `max_iter`; `enabled`,
`tool_groups`, and new teammates apply to `pipeline` and `single`.

## Teammates in `feature` mode

The `feature` recipe ([USAGE.md](USAGE.md#feature-mode)) uses the same teammates with prompts for changing code that
already exists: `codebase_analyst` (the `map` stage), `product_analyst` (`spec`: criteria for the new behaviour and for
what must keep working), `solution_architect` (`impact`: the smallest change, as work packages with exact owned paths),
`backend_engineer` and `frontend_engineer` (`implement`: minimal diff, follow the project's conventions, only their
own paths), `quality_engineer` (`tests`: in the project's existing test style), `debugger` (repairs only new failures,
never the baseline's), and `code_reviewer` and `security_engineer` (`review`: the change since the starting commit).

## Teammates in `fix` mode

The `fix` recipe ([USAGE.md](USAGE.md#fixing-a-bug)) adds no teammate: `codebase_analyst` (`map`), then the `debugger` does
the work that is a bug's own: `triage` (read the report, the parsed trace, and the code; ranked hypotheses), `reproduce`
(a failing test or script in the project's test style; the controller runs it and requires red), `fix` (the smallest
change at the root cause; the reproduction's files are read-only for it), and the repair rounds of `verify`.
`code_reviewer` and `security_engineer` review the fix since the starting commit (`review_fix`: does it remove the cause
or hide the symptom, is the regression test meaningful). To change who triages or fixes, override the `debugger` or give
a custom teammate the `triage`, `reproduce`, or `fix` stage under `stages`.

## Teammates in `maintain` and `review`

No new teammates: each task of [`maintain`](USAGE.md#maintaining-a-project) is a recipe that gives existing ones a lane.

| Task / stage | Teammate | Lane |
|--------------|----------|------|
| `add-tests`: `tests` and the repair rounds of `verify` | `quality_engineer` | test files only (`write_scope: tests`, policy `tests_only`) |
| `refactor`: `refactor` | `backend_engineer` | everything but tests and manifests, at most `maintain.max_refactor_lines`; `debugger` repairs |
| `upgrade-deps`: `plan_upgrades`, `upgrade`, `verify` | `devops_engineer` | manifests and lockfiles (`write_scope: manifests`); it plans from the registries (`web` and the package tools when enabled) |
| `docs`: `docs` and repairs | `technical_writer` | documentation files only |
| `security-audit`: `audit`; `fix` (with `--fix`) | `security_engineer` (read-only, with the dev tools); `debugger` | nothing, unless `--fix` |
| `custom`: `work` | `generalist_engineer` | anything, verified against the baseline |
| `review` (the command and the stage after a change) | `code_reviewer`, `security_engineer` | read-only |

`codebase_analyst` maps the code for the tasks that read it. To change who does a stage, override the teammate
(`[team.<key>]`) or write a recipe of your own ([USAGE.md](USAGE.md#your-own-recipes)); a recipe may name any teammate on
the team and is refused if it names one who is not.
