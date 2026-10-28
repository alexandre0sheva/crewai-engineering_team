import re
import shutil
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, toolchain

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "fixture"
CONFIG = 'export default { test: { include: ["__bench__/**/*.test.tsx"] } };\n'


def vitest(ws, *args):
    return toolchain(ws, "node", "npx", "--no-install", "vitest", "run", *args, node_modules=True)


def hidden(ws, group):
    """Run one describe() group of the hidden Vitest file against the workspace."""

    (ws / "__bench__").mkdir(exist_ok=True)
    shutil.copyfile(HERE / "hidden.test.tsx", ws / "__bench__" / "hidden.test.tsx")
    (ws / "__bench__.config.ts").write_text(CONFIG)
    result = vitest(ws, "--config", "__bench__.config.ts", "-t", group)
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    expect(result.returncode == 0, f"{group}:\n{output[-1200:]}")


def check_frozen_lines(ws):
    hidden(ws, "frozen-lines")


def check_props_untouched(ws):
    hidden(ws, "props-untouched")


def check_summary_unchanged(ws):
    hidden(ws, "summary-unchanged")


def count_tests(output):
    found = re.search(r"Tests\s+(\d+) passed", re.sub(r"\x1b\[[0-9;]*m", "", output))
    return int(found.group(1)) if found else 0


def check_regression_test(ws):
    own = vitest(ws)
    expect(own.returncode == 0, f"the suite must pass on the fixed code:\n{own.stdout[-800:]}")
    expect(count_tests(own.stdout) > 2, "the suite had 2 tests; a regression test must add one")
    with tempfile.TemporaryDirectory(prefix="unfixed-", dir=ws.parent) as scratch:
        unfixed = Path(scratch) / "project"
        shutil.copytree(FIXTURE, unfixed)
        shutil.rmtree(unfixed / "tests")
        shutil.copytree(ws / "tests", unfixed / "tests")
        for name in ("vitest.config.ts", "package.json"):
            if (ws / name).is_file():
                shutil.copyfile(ws / name, unfixed / name)
        before = vitest(unfixed)
    expect(before.returncode != 0, "the team's tests also pass on the unfixed code")


if __name__ == "__main__":
    main()
