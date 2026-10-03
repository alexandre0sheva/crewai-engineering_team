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
| `solution_architect` | Designs the solution and splits the work (`plan` stage); writes `docs/architecture.md`. | `worker` | read tools as above, plus `fs_write` |
| `backend_engineer` | Foundation, backend work packages, integration. | `worker` | `fs_read`, `fs_write`, `search`, `command`, `dev`, `runtime`, `code_intel`, `git_read`, `board`, `notes`, `human`, `knowledge`, `web`, `mcp:docs` |
| `frontend_engineer` | Frontend work packages. | `worker` | as `backend_engineer`, plus `browser` |
| `quality_engineer` | Repairs failing checks, writes tests, the release report. | `worker` | as `frontend_engineer` (it needs `browser` to look at the UI) |
| `generalist_engineer` | Does everything alone (`single` strategy) and stands in for a missing teammate. | `worker` | as `frontend_engineer` |

Groups marked `web` and `browser` are used only when they exist: `web` needs `web.enabled` (and the
teammate in `web.roles`, if set), `browser` needs the optional Playwright extra. A group that is not
available is left out of the run silently; `engineering-team doctor` says what is missing.

Two choices differ from a strict "least privilege" table, because the pipeline's stages need them:
the architect keeps `fs_write` (it writes the architecture document) and the quality engineer keeps
`fs_write` (tests and the release report). Everyone has the coordination groups (`board`, `notes`,
`human`) and `knowledge` (Search Docs, which is also how reference documents from `--context-dir` are
read).

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
