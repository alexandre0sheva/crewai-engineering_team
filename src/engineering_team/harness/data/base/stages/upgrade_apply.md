---
expected_output: >
  The manifests edited for exactly this group, and a short summary of the edits and anything
  that could not be applied.
---
Apply this group of dependency upgrades to the existing project {project_name} in
{workspace_path}:

{upgrades}

Edit the dependency manifest that declares each package (pyproject.toml, requirements*.txt,
package.json, Cargo.toml, go.mod, pom.xml, ...) to the target version, keeping its style
and any version range convention the file uses. You may change manifests and lockfiles only;
the file tools refuse anything else and the controller checks the diff. Regenerate a
lockfile only with the project's own package manager when it works offline; the controller
installs the dependencies and runs the project's checks itself afterwards, and undoes the
whole group if they fail, so do not try to fix application code to make an upgrade fit.
Reply with a short summary of the edits, and name any upgrade you could not apply and why.

{handoff}
{card}
{notes}
{resume_note}
