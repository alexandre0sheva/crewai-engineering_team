# `shop report` crashes on an empty cart

Running `python -m shop report --file cart.json` crashes with a `ZeroDivisionError` (the trace
is attached) when `cart.json` contains an empty list, `[]`.

Expected: the report is printed as `Items: 0, total: 0.00, average: n/a` and the command exits
with status 0. `Cart.average_price()` should return `None` for an empty cart instead of
raising. Reports for carts with items must stay exactly as they are, and so must the errors for
an unreadable or invalid file.

Please fix it and add a regression test to the project's test suite
(`python -m unittest discover -s tests`).
