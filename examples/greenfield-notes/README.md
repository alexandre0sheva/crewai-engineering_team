# Greenfield: a notes command-line tool

[`request.md`](request.md) asks for a small Python notes CLI (`add`, `list`, `search`, `delete`, tags,
a JSON file) and states every behaviour that matters, which is what makes a request something the team
can succeed with ([how to write one](../../docs/USAGE.md#writing-a-strong-request)).

```bash
uv run engineering-team new --request-file examples/greenfield-notes/request.md --project-name notes
```

## What a successful run leaves behind

In `workspace/notes/` (a Git repository of its own, one commit per finished stage):

- the package `notes/` with a `__main__.py`, so `python -m notes add "hello"` works from the project root;
- tests that the team's own verification stage ran and the controller re-ran itself;
- `README.md` and `docs/` (`spec.md`, `architecture.md`, `verification.md`, `release-report.md`);
- a verdict of **verified** in `docs/verification.md` and in the run report.

The benchmark judges the same request with hidden behavioural checks (ids never reused after a delete, tag
filters, exit codes): [`benchmarks/tasks/notes-cli`](../../benchmarks/tasks/notes-cli).

## The committed report

[`report.html`](report.html): the run as the controller recorded it (stages, board, parallel lanes,
checks, cost). Produced by a scripted run, see [the examples README](../README.md#about-the-committed-reports).
