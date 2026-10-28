# durations

Add up and summarise durations written like `1h30m`, `45s` or `2d4h`.

```bash
node src/cli.ts total 1h30m 45m        # 2h15m
node src/cli.ts stats --file laps.txt  # count, total, fastest, slowest, average
```

Units: `d` (days), `h`, `m`, `s`. Amounts may have decimals (`1.5h`). Results are rounded to the
nearest second and printed largest unit first, leaving out zero parts (`0s` for nothing).

Errors print `error: <message>` and exit with status 2.

Run the tests with `npm test` (Node 24 runs the TypeScript files directly).
