# wordstat: a minimum word length and JSON output

Please add two flags to `wordstat`:

- `-min-length N` skips words shorter than N characters (letters, digits and apostrophes; count
  characters, not bytes, so `été` has length 3). The default, 1, counts every word as today.
  `-top` applies after the filter. N below 1 is an error: `wordstat: -min-length must be 1 or more`
  on standard error, exit status 2.
- `-format text|json`. `text` (the default) is today's output. `json` prints a single line: a JSON
  array of `{"word": "...", "count": N}` objects in the same order as the text output (`[]` when there
  are no words). Any other value is an error: `wordstat: -format must be text or json`, exit status 2.

Both flags work with files and with standard input. Everything else stays as it is, including
the text output, the existing error messages and exit codes. Add table-driven tests for the new
flags (`go test ./...`).
