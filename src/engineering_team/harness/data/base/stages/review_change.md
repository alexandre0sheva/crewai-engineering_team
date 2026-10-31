---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Review the CHANGE the team made to the existing project {project_name} in
{workspace_path}. You can only read it: do not write or change any file, and do not report
anything you have not seen in the code.

Specification:

{spec}

The team started from commit {base}. Read the change with Git Diff Between Refs (first
{base}, second HEAD; start with stat=true) and Git Info diff for anything not committed
yet, then read the changed files in full where the diff is not enough, and the code that
calls them. Judge the change, not the whole project: report only defects the change
introduced or left behind, each once, with the file and line, a severity (critical, high,
medium, low, info) that reflects its impact, and a concrete suggested fix. Also report as
low or medium any part of the diff that is noise (unrelated reformatting, renames, files
nobody asked for) and any acceptance criterion the change does not deliver. Leave out style
preferences. If you find nothing, say so with an empty list. If you ran a tool, name it and
what it reported; if a tool could not run, say that instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
