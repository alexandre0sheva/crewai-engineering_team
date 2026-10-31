---
expected_output: >
  A JSON upgrade plan: upgrades (package, manifest, current, target, reason) and skipped
  (package and reason).
---
Plan the dependency upgrades for the existing project {project_name} in {workspace_path}.
Do not edit any file in this step: you decide what to try, the next step applies it in groups
and the controller checks each group with the project's own tests.

The request (which dependencies, and how far, if the person said):

{requirements}

{context}

Read the dependency manifests and lockfiles (the Inspect Dependencies tool lists the declared
and locked versions) and find newer versions: ask the package registries with the package
information and web tools when they are available, or the project's package manager through
the command tool (for example `pip list --outdated`, `npm outdated`, `cargo outdated`)
when it works offline. Propose an upgrade only to a version you found, never one you assume.
Prefer, in this order: versions that fix a known vulnerability, patch and minor updates, and
only then majors (a major is one upgrade of its own, with the migration risk in the reason).
Return the plan as JSON: each upgrade names the package, the manifest that declares it
(relative path), the current version, the target version, and why. List in skipped every
dependency you chose to leave alone, each with the reason (pinned on purpose, no newer
version, abandoned). Order the upgrades with the safest first.

{handoff}
{card}
{notes}
{resume_note}
