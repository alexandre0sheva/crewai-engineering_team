import json
import re
from pathlib import Path

from engineering_team.bench.checklib import expect, main

TASK = Path(__file__).resolve().parents[1]
FIXTURE = TASK / "fixture"
KEY = json.loads((TASK / "acceptance" / "answers.json").read_text())
CITATION = re.compile(r"([\w./-]+\.\w+):(\d+)(?:\s*[-–]\s*(\d+))?")
SLACK = 2


def sections(ws):
    path = ws / "ANSWERS.md"
    expect(path.is_file(), "there is no ANSWERS.md at the top of the project")
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"^##\s+(Q\d)\b.*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def citations(text):
    for file, first, last in CITATION.findall(text):
        yield file.removeprefix("./"), int(first), int(last or first)


def answered(ws, question):
    found = sections(ws)
    expect(question in found, f"no section ## {question} in ANSWERS.md")
    text = found[question]
    lowered = text.lower()
    for alternatives in KEY[question]["facts"]:
        expect(
            any(word.lower() in lowered for word in alternatives),
            f"{question} does not mention {' or '.join(alternatives)}",
        )
    for file, first, last in citations(text):
        for cite in KEY[question]["cites"]:
            low, high = cite["lines"]
            if (file == cite["file"] or file.endswith("/" + cite["file"])) and (
                first <= high + SLACK and last >= low - SLACK
            ):
                return
    places = ", ".join(
        f"{c['file']}:{c['lines'][0]}-{c['lines'][1]}" for c in KEY[question]["cites"]
    )
    expect(False, f"no citation in {question} points at the code that answers it ({places})")


def check_q1_free_shipping(ws):
    answered(ws, "Q1")


def check_q2_heavy_orders(ws):
    answered(ws, "Q2")


def check_q3_carrier(ws):
    answered(ws, "Q3")


def check_q4_delivery(ws):
    answered(ws, "Q4")


def check_citations_real(ws):
    text = (ws / "ANSWERS.md").read_text(encoding="utf-8", errors="replace")
    found = list(citations(text))
    expect(found, "ANSWERS.md cites no code")
    for file, first, last in found:
        path = ws / file
        expect(path.is_file(), f"{file}:{first} cites a file that does not exist")
        length = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        expect(1 <= first <= last <= length, f"{file}:{first}-{last} is outside its {length} lines")


def check_code_unchanged(ws):
    for path in FIXTURE.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            mine = ws / path.relative_to(FIXTURE)
            expect(mine.is_file(), f"{mine.relative_to(ws)} was deleted")
            expect(mine.read_bytes() == path.read_bytes(), f"{mine.relative_to(ws)} was changed")
    extra = [
        p.relative_to(ws).as_posix()
        for p in ws.rglob("*.py")
        if not (FIXTURE / p.relative_to(ws)).exists() and "__pycache__" not in p.parts
    ]
    expect(not extra, f"code was added: {extra}")


if __name__ == "__main__":
    main()
