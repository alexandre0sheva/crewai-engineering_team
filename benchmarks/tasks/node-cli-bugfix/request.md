# `durations total` prints `59m60s`

`node src/cli.ts total 1.5m 58m 29.5s` prints `59m60s`. Those durations add up to 3599.5 seconds,
which rounds to 3600 seconds, so it should print `1h`. The same happens elsewhere: a total of
59.5 seconds prints `60s` instead of `1m`, and `stats` shows the same kind of value for its total,
fastest, slowest and average lines.

Expected: a result is rounded to the nearest second once, and then printed as usual (largest unit
first, zero parts left out, `0s` for nothing); no part may ever read `60s`, `60m` or `24h`. Everything
else must stay exactly as it is: parsing, the error messages and exit status 2, and every output
that is already right.

Please fix it and add a regression test to the project's test suite (`npm test`, which runs
`node --test`).
