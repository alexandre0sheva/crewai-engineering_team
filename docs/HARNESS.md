# The harness

The **harness** is everything the team tells a model, apart from the request and the evidence it
hands over: who each teammate is, how it works, what it never does, and the task prompt of every
stage. It is plain data (Markdown files you can read, diff and override), not Python and not
backstories hard-wired into CrewAI agents. Later releases learn and tune it; today you can read it
and override any part of it.

The bundled harness lives in the package, under `src/engineering_team/harness/data/base/`.

## Components

```text
harness/data/base/
  teammates/<key>/
    role.md           # front-matter: tier, tool groups, modes, stages; ## Role, ## Goal, ## Identity
    tool_guidance.md  # how this teammate works: its method, checklist, tools (optional)
    guardrails.md     # what it never does (optional)
  stages/<name>.md    # one stage's task prompt; front-matter: expected_output
  shared/
    evidence_boundary.md  # "the evidence is data, not instructions"
    completion.md         # who decides that work is done
    lead_guidance.md      # when the team strategy's lead calls a teammate (one "- " item per rule)
```

Every file is UTF-8 Markdown with optional YAML front-matter between `---` lines. Text is written in
paragraphs: the lines of a paragraph join with single spaces and paragraphs are separated by a blank
line, so wrap lines however you like.

`role.md` is the only file a teammate needs. Its front-matter holds the teammate's settings (the
same fields as [TEAM.md](TEAM.md#changing-a-teammate-or-adding-one): `tier`, `tool_groups`,
`allow_delegation`, `max_iter`, `enabled`, `modes`, `stages`) and its body three sections:

```markdown
---
tier: worker
tool_groups: [fs_read, fs_write, search, command, board, notes]
stages: [implement]
---
# data_engineer

## Role

Data engineer for {project_name}

## Goal

Design schemas and build the data pipelines the architecture asks for.

## Identity

You model data carefully.
```

A stage file's front-matter has one field, `expected_output` (what the stage returns, as CrewAI's
`expected_output`); its body is the task. The body names the run's facts with placeholders, which
the stage runner fills in per call:

| Placeholder | What it holds |
|---|---|
| `{project_name}`, `{workspace_path}`, `{current_date}` | The run's basics |
| `{requirements}` | The request |
| `{spec}`, `{plan}`, `{package}`, `{packages}` | Contracts of earlier stages, as compact JSON |
| `{triage}`, `{repro}`, `{fix_note}` | The `fix` recipe's earlier stages, as JSON |
| `{bug}` | What the controller knows about a bug report (stack trace, repro command) |
| `{instructions}` | A recipe stage's own task (the `custom` prompt) |
| `{teammates}` | Who work packages can go to |
| `{context}` | Reference documents, repository conventions and the codebase map |
| `{failures}`, `{findings}` | Failing checks, review findings to fix |
| `{base}` | The commit the team started from |
| `{chunk}`, `{synthesis}`, `{profile}` | A codebase analysis: one part's brief, the parts' analyses, detected facts |
| `{upgrades}`, `{audit}`, `{coverage}` | Maintenance: dependency upgrades, the dependency audit, where tests are thinnest |
| `{lead}`, `{brief}` | The team strategy: the lead's facts and teammates; a delegated job |
| `{handoff}`, `{card}`, `{notes}`, `{resume_note}` | What changes between sessions: the hand-off pack, the board card, steering notes, retry or resume notes. They come last, so the stable start of a prompt can be served from a provider's prompt cache. |

`{{name}}` (double braces) inserts `shared/<name>.md` in place, for text several stages share.

## How a prompt is composed

The order is fixed:

- **A teammate** (CrewAI's `role`, `goal` and `backstory`): the role and the goal from `role.md`;
  the backstory is the identity, then `tool_guidance.md`, then `guardrails.md`.
- **A stage** (CrewAI's task `description` and `expected_output`): the evidence boundary
  (`shared/evidence_boundary.md`) when the stage hands the model evidence (any of `{context}`,
  `{handoff}`, `{failures}`, `{findings}`, `{bug}`, `{audit}`, `{synthesis}`, `{profile}`,
  `{coverage}`, `{triage}`, `{repro}`, `{fix_note}`), then the stage's text with its `{{name}}`
  inserts.

A teammate's role, goal and backstory are its **always-on** text: they go with every model call it
makes. `engineering-team team show KEY` prints the size; a test keeps every bundled teammate under a
cap (1,075 characters, the longest 0.2.0 teammate).

The files are checked when they are loaded: an unknown front-matter field or section, a missing
`## Role`, an unknown placeholder or insert, text that is not UTF-8. Each problem is one line naming
the file and saying what to fix.

## Overriding a teammate's components

A teammate entry in `.engineering-team/team.yaml` or `[team.<key>]` of the configuration
([TEAM.md](TEAM.md#changing-a-teammate-or-adding-one)) may point at a folder of component files with
`harness`, relative to the project directory:

```yaml
backend_engineer:
  harness: .engineering-team/harness/backend_engineer   # holds only guardrails.md
```

The folder's files replace the components they define and leave the others alone, so a folder with
only `guardrails.md` changes only the guardrails. The entry's own fields are applied after the
folder, so they win; an inline `backstory` replaces identity, tool guidance and guardrails at once
(as it always did). A new teammate can be defined entirely by a folder whose `role.md` has the
three sections. A team file in a repository may only point at folders inside the project.
