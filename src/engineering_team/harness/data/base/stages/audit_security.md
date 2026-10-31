---
expected_output: >
  A JSON review report: a one- or two-sentence summary and a findings list (each finding has
  severity, summary, file, line, and suggested_fix).
---
Audit the security of the existing project {project_name} in {workspace_path}. You can only
read it: do not write or change any file, and do not report anything you have not seen in the
code.

The request (the focus, if the person said):

{requirements}

{audit}

{context}

Read the code with the eyes of an attacker: where untrusted input enters (requests, files,
environment, command line, deserialization), where it reaches a dangerous sink (SQL, shell,
paths, templates, eval, redirects, outbound requests), authentication and authorization checks
and where they are missing, secrets in code or history, unsafe defaults, weak cryptography,
unsafe dependency use, and logging of sensitive data. Use the search, symbol, reference and
Git history tools to trace flows end to end; run the dependency tools if they help. The
dependency audit above was run by the controller: do not repeat its findings, but say what a
vulnerable dependency means for how this project uses it when you can see it. Report each
real problem once, with the file and line, a severity (critical, high, medium, low, info)
that reflects exploitability and impact, and a concrete suggested fix. Leave out theoretical
issues you cannot point to in the code. If you find nothing, say so with an empty list.

{handoff}
{card}
{notes}
{resume_note}
