---
name: codebase-reading
description: Recording a codebase's conventions and risks.
teammates: [codebase_analyst]
---
# Reading a codebase

You record the conventions the code actually follows (naming, error handling, test style, layout)
so later changes can match them, and you flag risks you can point to: missing tests, tangled
modules, hotspots with heavy churn, unsafe defaults.

Name each convention with two or three files that follow it; a convention seen once is an
example, not a convention.
