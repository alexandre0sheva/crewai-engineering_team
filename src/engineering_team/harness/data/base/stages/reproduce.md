---
expected_output: >
  A JSON reproduction (command, files, kind, expected_failure, summary, questions), with the
  failing test or script written in the workspace.
---
Reproduce the bug in the existing project {project_name} in {workspace_path}: write a test
or a small script that FAILS because of the bug and would pass once it is fixed.

The bug report:

{requirements}

{bug}

Triage (the debugger's earlier reading, not evidence; "(not available)" when the task was
small enough to skip it, so find the cause yourself first):

{triage}

{context}

Follow the project's own test conventions: put the test where similar tests live, in the
framework and style they use, and name it for the behaviour (a regression test that stays in
the project). Use a script only when no test can show the bug (a crash on start-up, a command
line); keep it small and put it next to the code it exercises. Make the assertion state the
RIGHT behaviour, so the test fails today with a message that shows the wrong one. Do not
change any other file and do not fix the bug: the controller runs your reproduction on the
project as it is, and accepts it only if it fails because of the bug (a test that passes,
cannot be collected, cannot import something, or hangs is not a reproduction). Run it
yourself first and look at the failure: is it the bug, and not a typo in your test?

Return the exact command line that runs your reproduction (one command, no shell operators,
for example `python -m pytest tests/test_notes.py::test_search_keeps_order`), the files you
created or changed for it (relative paths), kind (test or script), what the failure says
(expected_failure), and a one-line summary. If you cannot reproduce the bug from what you
were given after a real try, return no command and put your concrete questions for the
person in questions (what input, what environment, what output); never invent a bug you
did not see.

{handoff}
{card}
{notes}
{resume_note}
