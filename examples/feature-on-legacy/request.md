# Low-stock report for the inventory service

The warehouse team needs a low-stock report from our inventory service (the WSGI app in
`inventory/app.py`; run its tests with `python -m unittest discover -s tests`).

Add `GET /items/low-stock?threshold=N`:

- It answers `200` with a JSON array of the items whose `qty` is **strictly below** N, sorted by
  `qty` ascending, then by `name` ascending ignoring case, then by `id`. Each element has the same
  fields as an item has everywhere else in the API.
- `threshold` is optional and defaults to `10`.
- `threshold` must be a non-negative integer. Anything else (`abc`, `-1`, `2.5`, an empty value)
  is a `400` with a JSON body `{"error": "..."}`.

Do not change how the existing endpoints behave, and keep using only the standard library. Add
tests for the new endpoint to the project's test suite.
