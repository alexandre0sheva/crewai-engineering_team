# Parser fixtures

Tiny outputs of the tools `engineering_team.devtools` parses.

**Captured from real runs** (pytest 9, `unittest`, Ruby minitest, ruff 0.12, mypy 1.11; the work
directory was rewritten to `/work/project/`): `pytest_failures.xml`, `pytest_collection_error.xml`,
`unittest.txt`, `minitest.txt`, `ruff.json`, `ruff_format_check.txt` (ruff 0.12), `ruff_format_check_new.txt` (ruff 0.16), `mypy.txt`.

**Written by hand to the tool's documented format**, because the toolchain is not installed on the
machine that wrote them (jest, vitest, go, cargo, dotnet, RSpec, Maven, PHPUnit, eslint, tsc,
pyright, rubocop, phpcs, golangci-lint, clippy, coverage formats, audit tools): everything else.
If a real run disagrees with one of these, replace the fixture with the real output and fix the parser.
