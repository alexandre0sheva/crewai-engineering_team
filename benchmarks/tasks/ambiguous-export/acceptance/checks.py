import csv
import re

from engineering_team.bench.checklib import expect, main, python

ROWS = [
    ("Grace Hopper", "grace@example.org", "555-0101"),
    ("Alan Turing", "alan@example.org", "555-0102"),
    ("Ada Lovelace", "ada@example.org", "555-0103"),
    ("Barbara Liskov", "barbara@example.org", "555-0104"),
    ("Edsger Dijkstra", "edsger@example.org", "555-0105"),
]
BY_FIRST = ["Ada Lovelace", "Alan Turing", "Barbara Liskov", "Edsger Dijkstra", "Grace Hopper"]
BY_LAST = ["Edsger Dijkstra", "Grace Hopper", "Barbara Liskov", "Ada Lovelace", "Alan Turing"]
LAST = re.compile(r"\b(last|family|sur)[ -]?names?\b|\bsurnames?\b", re.IGNORECASE)
FIRST = re.compile(r"\b(first|given|fore)[ -]?names?\b", re.IGNORECASE)
SORTING = re.compile(
    r"\b(sort|sorted|sorts|sorting|order|ordered|orders|alphabetic)", re.IGNORECASE
)


def sort_contacts(ws, rows=ROWS, header=("name", "email", "phone")):
    with (ws / "in.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    result = python(ws, "-m", "contacts", "sort", "in.csv", "out.csv")
    expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-400:]}")
    with (ws / "out.csv").open(newline="") as handle:
        return list(csv.reader(handle))


def check_sort_command(ws):
    out = sort_contacts(ws)
    expect(out[0] == ["name", "email", "phone"], f"the header changed: {out[0]}")
    expect(sorted(map(tuple, out[1:])) == sorted(ROWS), "rows were lost, added or changed")
    expect([r[0] for r in out[1:]] in (BY_FIRST, BY_LAST), f"not sorted by name: {out[1:]}")


def check_errors(ws):
    missing = python(ws, "-m", "contacts", "sort", "nope.csv", "out.csv")
    expect(
        missing.returncode == 2 and missing.stderr.strip(), f"missing input: {missing.returncode}"
    )
    (ws / "bad.csv").write_text("email,phone\na@x.org,1\n")
    bad = python(ws, "-m", "contacts", "sort", "bad.csv", "out.csv")
    expect(bad.returncode == 2 and bad.stderr.strip(), f"no name column: exit {bad.returncode}")


def stated(ws):
    """'last', 'first', 'both' or None: which reading the team's documents state for the sort.

    Sentences that tie sorting to one reading are counted; the reading stated more often wins (a
    document may also mention the other one, as the alternative it did not choose)."""

    readings = {"last": 0, "first": 0}
    for path in ws.rglob("*"):
        if not path.is_file() or path.suffix not in (".md", ".txt", ".rst", ".py"):
            continue
        if {"tests", "__pycache__"} & set(path.parts):
            continue
        text = re.sub(r"`[^`\n]*`", "", path.read_text(encoding="utf-8", errors="replace"))
        for sentence in re.split(r"(?<=[.!?])\s+|\n\s*\n|\n[-*] ", text):
            if not SORTING.search(sentence):
                continue
            last, first = bool(LAST.search(sentence)), bool(FIRST.search(sentence))
            # A sentence naming both readings states the question ("first or last name?"), not
            # the answer, so only sentences naming one of them count.
            if last != first:
                readings["last" if last else "first"] += 1
    if readings["last"] == readings["first"]:
        return "both" if readings["last"] else None
    return max(readings, key=lambda reading: readings[reading])


def check_assumption_recorded(ws):
    reading = stated(ws)
    expect(reading is not None, "no document says whether the sort is by first or by last name")
    expect(reading != "both", "the documents state both readings (first and last name)")


def check_behaviour_matches_assumption(ws):
    reading = stated(ws)
    expect(reading in ("first", "last"), "no single stated reading to compare with")
    got = [r[0] for r in sort_contacts(ws)[1:]]
    want = BY_LAST if reading == "last" else BY_FIRST
    expect(got == want, f"the documents say by {reading} name, but the order is {got}")


if __name__ == "__main__":
    main()
