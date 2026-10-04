# CSV validator

Build a command-line CSV validator in Python 3.11 or newer, using only the standard library.

Run it from the project root as `python -m csvcheck SCHEMA.json DATA.csv` (a top-level package
`csvcheck/` with a `__main__.py` in the project root, not under `src/`).

## Schema

`SCHEMA.json` is a JSON object `{"columns": [ ... ]}`. Each column has a `name` and a `type`
(`string`, `int`, `float`, `date` as `YYYY-MM-DD`, or `bool` as `true` or `false`, lowercase),
and optionally:

- `required` (default false): an empty value is an error. When it is false, an empty value is
  accepted and no other rule is applied to it.
- `unique` (default false): the same non-empty value may not appear twice.
- `min` / `max`: inclusive bounds for `int` and `float` columns.
- `pattern`: a regular expression the whole value must match (`re.fullmatch`).
- `allowed`: a list of the only accepted values (compared as text).

The CSV has a header row. Every schema column must appear in it; extra columns are ignored.

## Output and exit status

Validate every row and report every error, one per line, in row order:
`row R, column NAME: MESSAGE`, where R is the 1-based number of the data record (the header is
not counted; a quoted field that spans several physical lines is one record) and MESSAGE says
what is wrong. A repeated unique value is reported on the later occurrence. A header without a
schema column is reported as `header: missing column NAME`.

- No errors: print `OK: N rows` (N data records) and exit 0.
- Errors: print them, then `FAILED: K error(s)`, and exit 1.
- The schema or CSV file cannot be read, the schema is not valid JSON or has the wrong shape,
  or the arguments are wrong: print a message to stderr and exit 2.

Add tests and a short README.
