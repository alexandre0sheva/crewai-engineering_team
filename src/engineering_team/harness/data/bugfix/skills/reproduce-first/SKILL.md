---
name: reproduce-first
description: "Fixing a bug: a failing test first."
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Reproduce first

1. Write the smallest test (or script) that shows the bug as reported, and run it: it must fail
   for the reason in the report, not for a typo or a missing import.
2. Only then change the code. The fix is done when that test passes and the tests around it still
   pass.
3. Keep the test: it is the regression test. Name it after the behaviour, not the ticket.
4. If you cannot make it fail, stop and say what you tried; a fix for a bug nobody reproduced is a
   guess.
