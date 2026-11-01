---
expected_output: >
  A JSON acceptance suite (tests: each with criteria, files, command, expected_failure and
  summary; untestable; summary), with the new test files written in the workspace.
---
Write the acceptance tests for a change to the existing project {project_name} in
{workspace_path}, BEFORE anyone implements it. Other teammates will implement the change and
cannot edit your tests, so the tests are the proof that it works.

Specification:

{spec}

Plan:

{plan}

{context}

First find how this project tests itself: the test directories, the framework, the naming and
fixture conventions, how existing tests for nearby code are written. Then write at least one
test per acceptance criterion, in exactly that style, in NEW test files (you may change test
files only, and no existing one). Test behaviour through the interfaces the plan names (the
module, function, endpoint or command the change adds or changes), so the test fails today and
passes once the change is made, without depending on how it is built. Assert the right
behaviour; never write a test that passes now.

The controller runs each test on the project as it is and keeps it only if it FAILS for the
right reason: an assertion about the missing behaviour, or an import of the symbol the change
will add. A test that passes, cannot be collected for another reason, has a syntax error, uses
a fixture or a package the project does not have, or hangs is rejected. Run each test yourself
and look at the failure before you return it.

Return one entry per test: the criteria it proves (ids such as AC-1), its new files (relative
paths), one command line that runs only this test (no shell operators, for example
`python -m pytest tests/test_search.py`), what its failure says today (expected_failure) and a
one-line summary. Put the criteria no automated test can show in untestable, as
"AC-3: why".

{handoff}
{card}
{notes}
{resume_note}
