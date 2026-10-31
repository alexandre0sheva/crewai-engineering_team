---
name: audit-method
description: Auditing a project for security and rating findings.
teammates: [security_engineer, generalist_engineer]
---
# Security audit method

1. Map the entry points: HTTP routes, CLI arguments, files read, environment variables, messages.
2. Follow each input to where it is used; a finding names the entry point and the sink.
3. Check configuration and secrets: debug flags, CORS, bind addresses, keys in the repository or in
   logs, TLS verification turned off.
4. Run the dependency audit when it can run; report the advisory id and the fixed version.
5. Rate by exploitability and impact: critical (remote, unauthenticated, data loss or takeover),
   high, medium, low. A theoretical issue with no path from an input is low or info.
