# Benchmarks

The task suite for `engineering-team bench` (repository only: it is not in the wheel or the sdist).
Each task is a request, a hidden acceptance check, a reference solution, and (for brownfield tasks)
a fixture. Method, task format, and threat model: [docs/BENCHMARKS.md](../docs/BENCHMARKS.md).

```bash
uv run engineering-team bench list
uv run engineering-team bench run --fake      # offline: validates the harness
uv run engineering-team bench spend           # the release's live spend (budget.toml, spend.jsonl)
```

Results go to `benchmarks/.runs/` (git-ignored).

Real-repository tasks: `bench import-swe` writes SWE-bench instances (`swe-starter.txt` lists a starter set) to
`tasks-swe/`, and their repositories are fetched into `.cache/` (both git-ignored); see
[docs/BENCHMARKS.md](../docs/BENCHMARKS.md#real-repository-tasks-swe-bench).
