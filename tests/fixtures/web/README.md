# Recorded registry responses

Real responses recorded on 2026-10-02 and trimmed to the fields the parsers read (the structure
is untouched):

- `pypi_six.json`: `https://pypi.org/pypi/six/json` (`info` subset and the first two `urls`).
- `npm_left_pad.json`: `https://registry.npmjs.org/left-pad/latest` (a *deprecated* package).
- `crates_itoa.json`: `https://crates.io/api/v1/crates/itoa` (crate subset, three versions).
- `go_protobuf_latest.json` / `go_protobuf.mod`: `https://proxy.golang.org/github.com/golang/protobuf/@latest`
  and its `.mod` file (a *deprecated* module).

Tests never touch the network; they parse these files.
