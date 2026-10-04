import hashlib
import re
import shutil
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"
SOURCE = Path("textkit") / "words.py"
# (old, new): each replaces text that occurs once in textkit/words.py with a plausible bug.
MUTANTS = [
    ("return len(text.split())", 'return len(text.split(" "))'),
    ('return slug.strip("-")', "return slug"),
    ("if len(text) <= limit:", "if len(text) < limit:"),
    ("if index > 0 and lower in SMALL_WORDS:", "if lower in SMALL_WORDS:"),
    ("char.lower() for char in text if char.isalnum()", "char for char in text if char.isalnum()"),
    ("if size < 1:", "if size < 2:"),
    ("key=lambda pair: (-pair[1], pair[0])", "key=lambda pair: (-pair[1],)"),
]
REQUIRED_KILLS = 6


def suite(directory):
    return python(directory, "-m", "unittest", "discover", "-s", "tests", timeout=60)


def tests_run(result):
    found = re.search(r"Ran (\d+) tests?", result.stderr)
    return int(found.group(1)) if found else 0


def check_tests_pass(ws):
    result = suite(ws)
    expect(result.returncode == 0, f"the suite fails or does not run:\n{result.stderr[-600:]}")
    expect(tests_run(result) >= 8, f"expected at least 8 tests, found {tests_run(result)}")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_source_untouched(ws):
    expected = {p.relative_to(FIXTURE): digest(p) for p in (FIXTURE / "textkit").rglob("*.py")}
    found = {p.relative_to(ws): digest(p) for p in (ws / "textkit").rglob("*.py")}
    expect(found == expected, "files under textkit/ were added, removed or changed")


def check_catches_defects(ws):
    source = (FIXTURE / SOURCE).read_text()
    killed, survived = 0, []
    for old, new in MUTANTS:
        expect(source.count(old) == 1, f"internal error: mutant text {old!r} must occur once")
        with tempfile.TemporaryDirectory(prefix="mutant-") as scratch:
            copy = Path(scratch) / "project"
            shutil.copytree(ws, copy, ignore=shutil.ignore_patterns("__pycache__"))
            (copy / SOURCE).write_text(source.replace(old, new))
            if suite(copy).returncode != 0:
                killed += 1
            else:
                survived.append(new)
    expect(
        killed >= REQUIRED_KILLS,
        f"the tests caught {killed} of {len(MUTANTS)} seeded defects (need {REQUIRED_KILLS}); "
        f"not caught: {survived}",
    )


if __name__ == "__main__":
    main()
