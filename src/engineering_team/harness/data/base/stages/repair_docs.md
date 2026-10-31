---
expected_output: >
  The non-documentation changes reverted, and a short summary.
---
The controller ran its own independent checks after documentation was written for
{project_name} in {workspace_path}, and some required ones failed (usually a policy: a file
that is not documentation was changed). {{completion}} You may change documentation files only.

What failed:

{failures}

Revert every change the policy lists (code, tests, configuration, comments) and keep the
documentation. Reply with a short summary of what you reverted.

{handoff}
{card}
{notes}
{resume_note}
