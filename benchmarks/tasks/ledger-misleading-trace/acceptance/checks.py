import json
import re
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"
RABO = """Datum,Bedrag,Munt,Naam tegenpartij,Tegenrekening IBAN/BBAN,Omschrijving-1
2026-03-01,,EUR,,,Saldo begin
2026-03-02,"-1.020,00",EUR,Woonstichting,,Huur maart
2026-03-03,"-64,35",EUR,Albert Heijn 1403,,Boodschappen
2026-03-20,"+12,40",EUR,Rabobank,,Rente maart
2026-03-25,"+2.750,00",EUR,Acme BV,,Salaris maart
2026-03-28,"+1.025,50",EUR,Belastingdienst,,Teruggave 2025
2026-03-31,,EUR,,,Saldo eind
"""
GENERIC = """date,amount,currency,description,counterparty,iban
2026-04-01,-42.10,EUR,Weekly shop,Albert Heijn 1234,
2026-04-02,+2500.00,EUR,Salary April,Acme BV,
2026-04-03,-9.99,EUR,Music,Spotify AB,
"""
ING = """Datum;Naam / Omschrijving;Tegenrekening;Af Bij;Bedrag (EUR);Mededelingen
20260502;Spotify AB;;Af;9,99;Ref: 7781
20260504;Gemeente Utrecht;;Bij;1.234,56;Teruggave
20260505;Jumbo;;Af;23,40;
"""


def ledgerly(ws, *args):
    return python(ws, "-m", "ledgerly", *args, timeout=60)


def imported(ws, name, text, fmt):
    (ws / name).write_text(text, encoding="utf-8")
    if not (ws / "ledger.json").exists():
        made = ledgerly(ws, "account", "add", "checking", "Joint account")
        expect(made.returncode == 0, f"account add failed: {made.stderr[-400:]}")
    return ledgerly(ws, "import", name, "--format", fmt, "--account", "checking")


def line_of(report, label):
    found = re.search(rf"^\s*{re.escape(label)}\s+(-?[\d,]+\.\d\d) EUR", report, re.MULTILINE)
    return found.group(1) if found else None


def check_march_report(ws):
    result = imported(ws, "march.csv", RABO, "rabo")
    expect(result.returncode == 0, f"import failed ({result.returncode}): {result.stderr[-400:]}")
    report = ledgerly(ws, "report", "income", "2026-03")
    expect(report.returncode == 0, f"the report failed:\n{report.stderr[-800:]}")
    for label, amount in (
        ("Salary", "2,750.00"), ("Interest", "12.40"), ("Other income", "1,025.50"),
        ("Total income", "3,787.90"), ("Total expenses", "-1,084.35"), ("Net", "2,703.55"),
    ):  # fmt: skip
        expect(
            line_of(report.stdout, label) == amount, f"{label} should be {amount}:\n{report.stdout}"
        )


def check_signed_amounts(ws):
    sys.path.insert(0, str(ws))
    from ledgerly.io.bank_formats import parse_amount

    for text, want in (
        ("+1.250,00", "1250.00"), ("+0,01", "0.01"), ("-1.234.567,89", "-1234567.89"),
        ("12,50", "12.50"), ("+12,50", "12.50"), ("-0,99", "-0.99"),
    ):  # fmt: skip
        got = parse_amount(text, decimal_comma=True)
        expect(got == Decimal(want), f"parse_amount({text!r}, decimal_comma=True) is {got!r}")
    expect(parse_amount("", decimal_comma=True) is None, "an empty cell is still None")


def check_unreadable_stops_import(ws):
    bad = RABO.replace('"-64,35"', '"64,3x"')
    result = imported(ws, "bad.csv", bad, "rabo")
    expect(result.returncode == 2, f"the import must stop with status 2, got {result.returncode}")
    expect("bad.csv:4" in result.stderr, f"the error must name bad.csv:4: {result.stderr[-300:]!r}")
    data = json.loads((ws / "ledger.json").read_text())
    expect(data["transactions"] == [], "lines from a file that failed to import were added")
    good = imported(ws, "march.csv", RABO, "rabo")
    expect(good.returncode == 0 and "5 added" in good.stdout, f"the good file: {good.stdout!r}")


def check_others_unchanged(ws):
    for name, text, fmt, summary in (
        ("april.csv", GENERIC, "generic", "april.csv: 3 read, 3 added, 0 already there"),
        ("may.csv", ING, "ing", "may.csv: 3 read, 3 added, 0 already there"),
    ):
        result = imported(ws, name, text, fmt)
        expect(result.stdout.strip() == summary, f"{fmt} import printed {result.stdout!r}")
    april = ledgerly(ws, "report", "income", "2026-04").stdout
    expect(line_of(april, "Net") == "2,447.91", f"April's net:\n{april}")
    may = ledgerly(ws, "report", "income", "2026-05").stdout
    expect(line_of(may, "Net") == "1,201.17", f"May's net:\n{may}")
    again = imported(ws, "may.csv", ING, "ing")
    expect("0 added, 3 already there" in again.stdout, f"re-import: {again.stdout!r}")
    for path in (FIXTURE / "tests").rglob("*.py"):
        mine = ws / "tests" / path.relative_to(FIXTURE / "tests")
        expect(
            mine.is_file() and mine.read_text() == path.read_text(), f"tests/{mine.name} changed"
        )
    original = sorted(f"tests.{p.stem}" for p in (FIXTURE / "tests").glob("test_*.py"))
    suite = python(ws, "-m", "unittest", *original, timeout=120)
    expect(suite.returncode == 0, f"the original tests fail:\n{suite.stderr[-600:]}")


def check_regression_test(ws):
    own = python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=120)
    expect(own.returncode == 0, f"the suite must pass on the fixed code:\n{own.stderr[-500:]}")
    ran = re.search(r"Ran (\d+) tests?", own.stderr)
    expect(ran and int(ran.group(1)) > 24, "the suite had 24 tests; a regression test must add one")
    with tempfile.TemporaryDirectory(prefix="unfixed-") as scratch:
        unfixed = Path(scratch) / "project"
        shutil.copytree(FIXTURE, unfixed)
        shutil.rmtree(unfixed / "tests")
        shutil.copytree(
            ws / "tests", unfixed / "tests", ignore=shutil.ignore_patterns("__pycache__")
        )
        before = python(unfixed, "-m", "unittest", "discover", "-s", "tests", timeout=120)
    expect(before.returncode != 0, "the team's tests also pass on the unfixed code")


if __name__ == "__main__":
    main()
