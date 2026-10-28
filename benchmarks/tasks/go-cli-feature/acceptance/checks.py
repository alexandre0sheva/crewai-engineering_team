import json
import re

from engineering_team.bench.checklib import expect, main, toolchain

TEXT = """The cat sat on the mat; the CAT ran.
Été, été! Don't stop: the café is open.
A cat's life is a long one, the longest.
"""


BINARY = ".bench-wordstat"


def build(ws):
    """Build the command once per check (``go run`` would turn every exit status into 1)."""

    if not (ws / BINARY).is_file():
        built = toolchain(ws, "go", "go", "build", "-o", BINARY, ".")
        expect(built.returncode == 0, f"go build failed:\n{built.stderr[-800:]}")


def wordstat(ws, *args, text=TEXT, file="sample.txt"):
    build(ws)
    (ws / "sample.txt").write_text(text, encoding="utf-8")
    return toolchain(ws, "go", f"./{BINARY}", *args, file)


def check_json_format(ws):
    result = wordstat(ws, "-format", "json", "-top", "3")
    expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-400:]}")
    lines = result.stdout.splitlines()
    expect(len(lines) == 1, f"json output must be one line, got {result.stdout!r}")
    want = [{"word": "the", "count": 5}, {"word": "a", "count": 2}, {"word": "cat", "count": 2}]
    expect(json.loads(lines[0]) == want, f"-format json -top 3 printed {lines[0]!r}")
    empty = wordstat(ws, "-format", "json", text="... !!!\n")
    expect(empty.returncode == 0 and json.loads(empty.stdout) == [], f"no words: {empty.stdout!r}")
    text = wordstat(ws, "-format", "text", "-top", "2")
    expect(text.stdout == "5\tthe\n2\ta\n", f"-format text printed {text.stdout!r}")


def check_min_length(ws):
    result = wordstat(ws, "-min-length", "4", "-top", "4")
    expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-400:]}")
    want = "1\tcafé\n1\tcat's\n1\tdon't\n1\tlife\n"
    expect(result.stdout == want, f"-min-length 4 -top 4 printed {result.stdout!r}")
    accents = wordstat(ws, "-min-length", "3", "-top", "0", "-format", "json")
    words = [item["word"] for item in json.loads(accents.stdout or "null") or []]
    expect("été" in words, "été has 3 characters, so -min-length 3 keeps it")
    expect("on" not in words and "a" not in words, "-min-length 3 drops shorter words")


def check_flag_errors(ws):
    for args, message in (
        (["-format", "xml"], "wordstat: -format must be text or json"),
        (["-min-length", "0"], "wordstat: -min-length must be 1 or more"),
    ):
        result = wordstat(ws, *args)
        expect(
            result.returncode == 2 and message in result.stderr and result.stdout == "",
            f"{' '.join(args)}: exit {result.returncode}, stderr {result.stderr[-300:]!r}",
        )


def check_defaults_unchanged(ws):
    result = wordstat(ws, "-top", "5")
    want = "5\tthe\n2\ta\n2\tcat\n2\tis\n2\tété\n"
    expect(result.stdout == want, f"-top 5 printed {result.stdout!r}")
    every = wordstat(ws)
    expect(len(every.stdout.splitlines()) == 10, "the default -top is 10")
    missing = wordstat(ws, file="nope.txt")
    expect(
        missing.returncode == 1 and "wordstat: cannot read nope.txt" in missing.stderr,
        f"a missing file: exit {missing.returncode}, {missing.stderr[-300:]!r}",
    )
    negative = wordstat(ws, "-top", "-1")
    expect(negative.returncode == 2 and "-top must be 0 or more" in negative.stderr, "-top -1")


def check_table_tests(ws):
    result = toolchain(ws, "go", "go", "test", "-v", "./...")
    expect(result.returncode == 0, f"go test must pass:\n{result.stdout[-1000:]}")
    runs = len(re.findall(r"^=== RUN", result.stdout, re.MULTILINE))
    expect(runs >= 8, f"the suite had 6 tests and subtests; it has {runs}: test the new flags")
    sources = "\n".join(p.read_text(errors="replace") for p in ws.rglob("*_test.go"))
    for flag in ("min-length", "json"):
        expect(flag in sources.lower(), f"no test mentions {flag}")


if __name__ == "__main__":
    main()
