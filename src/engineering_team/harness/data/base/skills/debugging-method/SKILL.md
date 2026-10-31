---
name: debugging-method
description: Finding the root cause of a failure.
teammates: [debugger, generalist_engineer]
---
# Debugging method

Your method: reproduce the failure first by running the failing command yourself; read the failing
code and its tests; form one hypothesis at a time and test it; fix the cause, not the symptom, with
the smallest change that does; run the failing command again and the surrounding tests.

When a hypothesis fails, say so in your notes and drop it before forming the next. A failure you
cannot reproduce is reported as such, with what you ran.
