---
name: go-table-tests
description: Go tests.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Go tests

- Table-driven: a slice of cases with a `name`, ranged over with `t.Run(tc.name, ...)`; each
  failure then names its case.
- `go test ./...` for everything, `go test ./pkg -run 'TestName/case' -v` for one case, `-count=1`
  to bypass the cache, `-race` for data races.
- Use `t.Helper()` in assertion helpers, `t.TempDir()` for files, `t.Setenv` for the environment,
  `t.Cleanup` for teardown.
- Compare with `reflect.DeepEqual` or the comparison package the module already uses; report
  `got` and `want` in the message.
- `t.Parallel()` cases must not share mutable state; copy the loop variable on Go before 1.22.
