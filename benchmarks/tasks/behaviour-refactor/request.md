# Refactor `orders/summary.py`

`summarize()` in `orders/summary.py` is one very long function that mixes validation, discounts,
tax and shipping, per-customer totals and the final report. Nobody dares touch it.

Refactor it into small, well-named functions (no function longer than 30 lines), keeping
`orders.summary.summarize(orders)` as the public entry point with exactly the same behaviour for
every input: the same results, in the same key order, the same floating-point rounding, the same
problems reported in the same order. The existing tests in `tests/` must keep passing and must not
be edited. Use only the standard library.
