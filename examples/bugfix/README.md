# Bug fix: `shop report` crashes on an empty cart

[`repo/`](repo) is a tiny cart-summary tool. [`request.md`](request.md) is the bug report and
[`trace.txt`](trace.txt) the stack trace that came with it (`ZeroDivisionError` in
`Cart.average_price`).

```bash
cp -R examples/bugfix/repo /tmp/shop
git -C /tmp/shop init -q -b main && git -C /tmp/shop add -A && git -C /tmp/shop commit -qm "Initial commit"
uv run engineering-team fix --repo /tmp/shop \
  --request-file examples/bugfix/request.md --trace-file examples/bugfix/trace.txt
```

## What a successful run leaves behind

Fix mode is built around evidence: the team first writes a reproduction that **fails** because of the bug
(red), the controller runs it and only accepts a real failure, then the team fixes the code and the same
reproduction must **pass** (green).

- a regression test that fails on the unfixed code and passes on the fixed code;
- `Cart.average_price()` returning `None` for an empty cart, and the report printing
  `Items: 0, total: 0.00, average: n/a` (exit status 0), with reports for carts with items unchanged;
- the red and green runs in the report's **Reproduction** check, on a branch of `/tmp/shop`.

If the team cannot reproduce a bug it stops and asks you, instead of guessing
([USAGE.md](../../docs/USAGE.md#fixing-a-bug)). Judged by hidden checks in
[`benchmarks/tasks/seeded-bug`](../../benchmarks/tasks/seeded-bug).

## The committed report

[`report.html`](report.html): the red/green reproduction, the checks, the board and the diff. Produced by
a scripted run, see [the examples README](../README.md#about-the-committed-reports).
