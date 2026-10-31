---
expected_output: >
  A JSON codebase map (overview, architecture, modules, key_flows, conventions, hotspots,
  risks, how_to_run, how_to_test).
---
Write the codebase map of {project_name} in {workspace_path} from the analyses of its
parts. You can only read the project: do not write or change any file.

Facts the controller found (they are certain; do not contradict them):

{profile}

The analyses of the parts (written by other analysts; check anything surprising against
the code before repeating it):

{synthesis}

Combine them into one map: an overview (what the project is and does); the architecture
(how the parts fit together); the modules that matter (name, path, purpose, what each
uses); the key flows end to end; the conventions a change must follow; the hotspots
(use the Hotspots tool when the project has Git history); the risks; and how to run and
how to test it, using the detected commands. Resolve disagreements between the analyses by
reading the code. Keep every item to one sentence and leave out what you cannot support.

{handoff}
{card}
{notes}
{resume_note}
