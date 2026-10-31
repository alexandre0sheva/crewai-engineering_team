---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Review a change to the project {project_name} in {workspace_path}. You can only read it: do
not write or change any file, and do not report anything you have not seen in the code.

The person's focus for this review, if any:

{requirements}

The change is everything since commit {base}: the commits on this branch and any uncommitted
work. Read it with Git Diff Between Refs (first {base}, second HEAD; start with stat=true)
and Git Info diff for what is not committed, then read the changed files in full where the
diff is not enough, and the code that calls them (the symbol, reference and import tools show
who depends on what). Judge the change, not the whole project: report only defects the
change introduced or left behind, each once, with the file and line, a severity (critical,
high, medium, low, info) that reflects its real impact, and a concrete suggested fix. Your
checklist covers correctness, error handling, security, data loss, concurrency, API and
compatibility breaks, missing or weak tests for the new behaviour, and maintainability that
will cost the next person. Leave out style preferences and anything the project's linter
owns. If you find nothing, say so with an empty list. If you ran a tool, name it and what it
reported; if a tool could not run, say that instead of guessing.

{handoff}
{card}
{notes}
{resume_note}
