# A tool to sort our contact list

Our club keeps its members in a CSV file with a header line and the columns `name`, `email` and
`phone`, for example:

```csv
name,email,phone
Grace Hopper,grace@example.org,555-0101
Alan Turing,alan@example.org,555-0102
```

Please build a small command-line tool, run as `python -m contacts sort INPUT.csv OUTPUT.csv`, that
writes the same rows to `OUTPUT.csv` sorted by name, with the same header and columns, so we can
print it as the club's phone list. It should exit with status 0 when it worked, and with status 2
and a message on standard error when the input file is missing or has no `name` column. Use only
the Python standard library, and include tests.
