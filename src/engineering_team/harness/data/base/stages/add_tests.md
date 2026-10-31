---
expected_output: >
  New test files in the workspace that pass on the unchanged code, and a short summary: the
  modules covered, what each test pins, behaviour that looks like a bug, and what is still
  untested.
---
Add characterization tests to the existing project {project_name} in {workspace_path}: tests
that pin what the code does TODAY, so a later change that alters behaviour fails them. You
may write test files and test support only; the file tools refuse anything else, and the
controller checks the diff. Do not change production code, even to make it testable.

The request (what to cover, if the person said):

{requirements}

{coverage}

{context}

First find how this project tests itself: the test directories, the framework, the naming and
fixture conventions, and tests written for nearby code. Then find the code with no tests (the
coverage list above, the symbol, reference and test-finding tools, and Git history show what is
untested and what changes often) and write tests for it in exactly that style: the public
behaviour of each untested module first, then its edge cases and error paths. Assert what the
code really does, even where that looks wrong: if a behaviour looks like a bug, pin it with
a comment saying so and report it in your summary, do not fix it. Tests must be deterministic
(no network, clock, or random without control) and fast. Run the new tests and make them pass
against the unchanged code; a test you cannot make pass honestly is dropped, not weakened.

{handoff}
{card}
{notes}
{resume_note}
