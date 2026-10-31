---
name: go-modules
description: Go dependencies and module layout (go.mod).
teammates: [backend_engineer, frontend_engineer, generalist_engineer, devops_engineer]
---
# Go modules

- `go get example.com/pkg@vX.Y.Z` to add or change a dependency, then `go mod tidy`; commit
  `go.mod` and `go.sum` together and never edit `go.sum` by hand.
- The `go` line in `go.mod` is the language version; do not raise it as a side effect.
- Packages under `internal/` are importable only from their parent tree.
- `go build ./...` and `go vet ./...` before you finish.
