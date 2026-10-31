---
expected_output: >
  The failing checks fixed by correcting the new tests, and a short summary of what changed.
---
The controller ran its own independent checks after characterization tests were added to
{project_name} in {workspace_path}, and some required ones failed. {{completion}} You may change test
files only, and the controller checks the diff.

What failed:

{failures}

A characterization test that fails on the unchanged code is wrong, not the code: fix the test
so it describes what the code really does (run it to see), or delete it if it cannot be made
deterministic. If the policy check lists files that are not tests, revert them. Never touch
production code and never weaken a test that passed before this work.

{handoff}
{card}
{notes}
{resume_note}
