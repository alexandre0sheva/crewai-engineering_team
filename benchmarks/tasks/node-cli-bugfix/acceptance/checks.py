import json
import re
import shutil
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, toolchain

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"


def node(ws, *args):
    return toolchain(ws, "node", "node", *args)


def evaluate(ws, code):
    """Run an ES module snippet that prints JSON; returns the value."""

    result = node(ws, "--input-type=module", "-e", code)
    expect(result.returncode == 0, f"the snippet failed: {result.stderr[-600:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def formatted(ws, values):
    code = (
        'import { formatDuration } from "./src/format.ts";'
        f"console.log(JSON.stringify({json.dumps(values)}.map(formatDuration)));"
    )
    return evaluate(ws, code)


def cli(ws, *args):
    return node(ws, "src/cli.ts", *args)


def check_carry_rounding(ws):
    cases = {59.5: "1m", 3599.5: "1h", 86399.6: "1d", 3659.5: "1h1m", 119.5: "2m", 7199.5: "2h"}
    got = formatted(ws, list(cases))
    for (value, want), have in zip(cases.items(), got, strict=True):
        expect(have == want, f"formatDuration({value}) is {have!r}, expected {want!r}")


def check_cli_output(ws):
    total = cli(ws, "total", "1.5m", "58m", "29.5s")
    expect(total.returncode == 0 and total.stdout == "1h\n", f"total printed {total.stdout!r}")
    (ws / "laps.txt").write_text("59.5s\n# warm-up\n\n29.75s\n")
    stats = cli(ws, "stats", "--file", "laps.txt")
    want = "count: 2\ntotal: 1m29s\nfastest: 30s\nslowest: 1m\naverage: 45s\n"
    expect(stats.returncode == 0 and stats.stdout == want, f"stats printed {stats.stdout!r}")


def check_unchanged(ws):
    cases = {3725: "1h2m5s", 90000: "1d1h", 0: "0s", 0.4: "0s", 61.2: "1m1s", 172800: "2d"}
    got = formatted(ws, list(cases))
    for (value, want), have in zip(cases.items(), got, strict=True):
        expect(have == want, f"formatDuration({value}) is {have!r}, expected {want!r}")
    expect(cli(ws, "total", "1h30m", "45m").stdout == "2h15m\n", "total 1h30m 45m is 2h15m")
    for args, message in (
        (["total", "10x"], 'error: unknown unit "x" in "10x"'),
        (["total", "1h 30m"], 'error: cannot read "1h 30m"'),
        (["total"], "error: usage: durations total DURATION... | durations stats --file FILE"),
        (["stats", "--file", "missing.txt"], "error: cannot read missing.txt"),
    ):
        result = cli(ws, *args)
        expect(
            result.returncode == 2 and result.stderr.strip() == message,
            f"{' '.join(args)}: exit {result.returncode}, stderr {result.stderr.strip()!r}",
        )
    (ws / "empty.txt").write_text("# nothing\n")
    empty = cli(ws, "stats", "--file", "empty.txt")
    expect(empty.returncode == 2 and "no durations in empty.txt" in empty.stderr, "empty file")


def count_tests(output):
    found = re.search(r"^\S* ?tests (\d+)", output, re.MULTILINE)
    return int(found.group(1)) if found else 0


def check_regression_test(ws):
    own = node(ws, "--test")
    expect(own.returncode == 0, f"the suite must pass on the fixed code:\n{own.stdout[-800:]}")
    expect(count_tests(own.stdout) > 6, "the suite had 6 tests; a regression test must add one")
    with tempfile.TemporaryDirectory(prefix="unfixed-", dir=ws.parent) as scratch:
        unfixed = Path(scratch) / "project"
        shutil.copytree(FIXTURE, unfixed)
        shutil.rmtree(unfixed / "test")
        shutil.copytree(ws / "test", unfixed / "test")
        before = node(unfixed, "--test")
    expect(before.returncode != 0, "the team's tests also pass on the unfixed code")


if __name__ == "__main__":
    main()
