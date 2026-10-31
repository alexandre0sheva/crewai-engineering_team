---
expected_output: >
  The tests in the workspace, passing, and a short summary: tests added or changed per
  criterion, commands run with their exit codes, and anything the change leaves untested.
---
Add or update the automated tests for the change to the existing project {project_name} in
{workspace_path}.

Specification:

{spec}

Plan:

{plan}

{context}

First find how this project tests itself: the test directories, the framework, the naming
and fixture conventions, and how existing tests for nearby code are written. Then write the
tests the change needs in exactly that style: at least one test per acceptance criterion
that describes behaviour, tests for the edge cases and error paths of the new code, and an
updated version of any existing test the change legitimately invalidates (say so in your
summary). Never weaken, delete, or skip an existing test to make it pass. Keep to test files
and the test support code; do not change the code under test (if it is wrong, say so in
your summary). Run the project's tests for what you added and fix your own tests until they
pass. Tests that were failing before the change (the baseline) are not yours to fix.

{handoff}
{card}
{notes}
{resume_note}
