---
id: les-ee66d32f
domain: new_project/*
component: teammates/generalist_engineer/tool_guidance
confidence: medium
evidence:
  run_id: 20261116-190139-27dde5
  verdict: verified
  check_ids:
  - tests
  revision: 372e130c8de9ed8f64ce9c2e6639e5e4
  rows:
  - behaviour: Used Apply Patch with an `edits` list whose keys did not match the tool schema ('old_text'/'new_text'
      instead of 'old'/'new'), four times in a row (#18-#21) after the first pytest run failed (#17).
    guidance: no guidance on the Apply Patch schema; the tool's own error message supplied the fix.
    effect: 'Four failed calls; the run recovered by switching to the unified-diff `patch` form (#22,
      #23) and tests then passed.'
    verdict: hurt
    evidence:
    - '#17'
    - '#18'
    - '#19'
    - '#20'
    - '#21'
    - '#22'
    - '#23'
created: '2026-11-16T19:18:30.309055Z'
review: auto
---
# Lesson les-ee66d32f (new_project/*)

## Cue

An agent must edit an existing file to fix a failing test, and the patch tool offers both a structured edits list and a unified diff; the agent does not know the structured form's required key names.

## Lesson

Add to teammates/generalist_engineer/tool_guidance a line stating that Apply Patch edits entries require string keys 'path', 'old' and 'new' (or that the unified-diff `patch` argument is the fallback), so agents stop retrying the same malformed edits list after the schema error.
