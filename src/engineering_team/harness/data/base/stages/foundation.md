---
expected_output: >
  A runnable project foundation with README.md. Report files changed, dependencies added,
  commands run with their exit codes, and the remaining work.
---
Create the shared project foundation for {project_name} in {workspace_path}.

Specification:

{spec}

Plan:

{plan}

Create or improve a conventional structure for the chosen stack: dependency and tool
configuration, a safe .gitignore, .env.example when configuration is needed, and a useful
README.md (setup, run, test, architecture notes). Implement the shared pieces every work
package will build on and the thinnest real vertical slice. Leave no placeholder functions
where working behaviour is feasible. Install only necessary dependencies and run the
cheapest relevant compile, import, or build check. Preserve compatible existing behaviour.

{handoff}
{card}
{notes}
{resume_note}
