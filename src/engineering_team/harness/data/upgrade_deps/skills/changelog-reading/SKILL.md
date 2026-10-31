---
name: changelog-reading
description: "Upgrading a dependency: breaking changes first."
teammates: [devops_engineer, generalist_engineer, backend_engineer, debugger]
---
# Reading a changelog before an upgrade

- Read the release notes (CHANGELOG, GitHub releases, a migration guide) for every version between
  the current one and the target, not only the target.
- Look for: removed or renamed APIs, changed defaults, a new minimum language or runtime version,
  and new peer dependencies.
- Search the code for each affected API before upgrading; upgrade one group at a time and run the
  tests after each.
- Major versions get their own step; an upgrade the code cannot take today is reported with the
  reason, not forced.
