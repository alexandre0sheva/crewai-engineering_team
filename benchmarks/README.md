# Benchmarks

The task suite for `engineering-team bench` (repository only: it is not in the wheel or the sdist).
Each task is a request, a hidden acceptance check, a reference solution, and (for brownfield tasks)
a fixture. Method, task format, and threat model: [docs/BENCHMARKS.md](../docs/BENCHMARKS.md).

```bash
uv run engineering-team bench list
uv run engineering-team bench run --fake      # offline: validates the harness
```

Results go to `benchmarks/.runs/` (git-ignored).
