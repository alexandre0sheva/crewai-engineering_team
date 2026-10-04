# Cron-like schedule calculator

Build a library and command in Python 3.11 or newer, using only the standard library, that
computes when a cron expression fires next.

Put a package `cronlite/` in the project root (not under `src/`). It provides:

- `cronlite.next_run(expression: str, after: datetime) -> datetime`: the first time strictly after
  `after` at which the expression fires. Datetimes are naive (no time zone); seconds and
  microseconds of `after` are ignored and the result has them at zero. Raise `ValueError` if the
  expression is invalid. (Import it as `from cronlite import next_run`.)
- `python -m cronlite "EXPRESSION" [--after YYYY-MM-DDTHH:MM] [--count N]`: prints the next N
  (default 1) fire times, one per line, formatted `YYYY-MM-DDTHH:MM`, starting after `--after`
  (default: now). An invalid expression or `--after` value prints a message to stderr and exits
  with status 2.

## Expressions

Five fields separated by whitespace: minute (0-59), hour (0-23), day of month (1-31), month
(1-12), day of week (0-6, where 0 is Sunday). Each field is a comma-separated list of parts; a
part is `*`, a number `N`, a range `A-B`, or a step `*/S` or `A-B/S` (every S-th value of the
range, starting at its start; `*/S` starts at the field's lowest value). Anything else, a value
out of range, a range with A greater than B, or a step of 0 is invalid.

Day of month and day of week combine as in classic cron: when both fields are restricted (neither
is `*`), a day matches if *either* matches; when one of them is `*`, only the other counts. A day
that never occurs (such as `0 0 30 2 *`) is still a valid expression; give up with `ValueError`
if nothing fires within 8 years.

Add tests and a short README.
