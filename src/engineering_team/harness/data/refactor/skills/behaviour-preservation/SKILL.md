---
name: behaviour-preservation
description: "Refactoring: keep behaviour identical."
teammates: [backend_engineer, frontend_engineer, generalist_engineer, debugger, code_reviewer]
---
# Preserving behaviour

- Before changing structure, make sure tests cover the code you will move; add characterization
  tests (they pin what the code does today, right or wrong) where they do not.
- Change structure in small steps and run the tests after each; never mix a refactor with a
  behaviour change or a dependency upgrade.
- Public names, signatures, outputs, error types and messages, and file formats stay the same
  unless the request says otherwise.
- A test that needs changing is a sign behaviour changed: stop and check.
