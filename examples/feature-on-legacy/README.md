# Feature on a legacy project: a low-stock report

[`repo/`](repo) is a small WSGI inventory service with its own tests, written the way a legacy service
often is (routes matched by hand). [`request.md`](request.md) asks for `GET /items/low-stock?threshold=N`
and says what must not change.

The team works on a branch, worktree or copy, never on your files directly, so start from a Git copy:

```bash
cp -R examples/feature-on-legacy/repo /tmp/inventory
git -C /tmp/inventory init -q -b main && git -C /tmp/inventory add -A && git -C /tmp/inventory commit -qm "Initial commit"
uv run engineering-team feature --repo /tmp/inventory --request-file examples/feature-on-legacy/request.md
uv run engineering-team diff --stat        # what the team changed
```

## What a successful run leaves behind

- a branch `engineering-team/<run-id>-…` of `/tmp/inventory` with the new endpoint in `inventory/app.py`
  and tests for it in `tests/`;
- the existing tests still passing: the controller ran them **before** the team started (the baseline)
  and again after, so a regression is a finding, not a surprise;
- `CHANGE_SUMMARY.md` in the run directory, and a verdict of **verified**;
- `engineering-team export-patch --out change.patch` gives the change as a patch for `git apply`.

Judged by hidden checks in [`benchmarks/tasks/legacy-feature`](../../benchmarks/tasks/legacy-feature).

## The committed report

[`report.html`](report.html), including the diff viewer. Produced by a scripted run, see
[the examples README](../README.md#about-the-committed-reports).
