---
expected_output: >
  A coherent project with accurate onboarding documentation and docs/release-report.md, and
  a concise delivery summary with the workspace path.
---
Perform a release-readiness review of {project_name} in {workspace_path}.

Specification:

{spec}

Re-read the request, the architecture, the controller's verification record
(docs/verification.md; read it, do not edit it), the README, and the important source files. Confirm the project is coherent, starts with its documented
commands, and delivers the primary vertical slice. Fix only targeted integration,
documentation, or configuration issues, then rerun the affected check. Make sure README.md
gives a new developer accurate setup, configuration, run, test, and troubleshooting steps.

Write docs/release-report.md: delivered scope, criteria status, architecture summary,
verification evidence, known limitations, security notes, and the next three improvements.
Do not claim production readiness when only MVP readiness was shown.

{handoff}
{card}
{notes}
{resume_note}
