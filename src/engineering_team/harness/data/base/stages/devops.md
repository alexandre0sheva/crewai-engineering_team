---
expected_output: >
  The delivery files in the workspace and docs/devops.md recording them and what was run.
---
Prepare {project_name} in {workspace_path} to be run and shipped.

Specification:

{spec}

Plan (it has the project's commands):

{plan}

Read the project and its README first, then add only what fits: a Dockerfile when the
project is a service or would benefit from one (pinned base image, non-root user, no
secrets baked in) and a .dockerignore; a CI workflow (for example .github/workflows/ci.yml)
that runs the project's own install, test, lint, and build commands; a complete
.env.example listing every environment variable the code reads, with placeholder values;
and short run scripts only where they save steps. Do not change application code. Run
whatever you can to check your files (the project commands, a Docker build if Docker is
available) and record what you could not run. Write docs/devops.md: each file you added or
changed and why, the commands you ran with their outcome, and what is unchecked.

{handoff}
{card}
{notes}
{resume_note}
