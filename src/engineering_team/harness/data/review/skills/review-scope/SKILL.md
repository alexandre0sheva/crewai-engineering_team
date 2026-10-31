---
name: review-scope
description: "Reviewing a change: the diff in its context."
teammates: [code_reviewer, security_engineer, generalist_engineer]
---
# Reviewing a change

- Start from the diff, then read each changed function in full and its callers: most defects are
  at the edges of the change, not in its lines.
- Check that the tests changed with the code and that they would fail without the change.
- Report only defects you can point to by file and line; leave style to the linter.
- Untouched code is out of scope unless the change breaks it.
