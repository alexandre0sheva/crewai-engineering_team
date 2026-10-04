"""The new-run -> run-page -> results path in a real browser, against ``ui --demo``.

Skipped (the ``browser`` marker) unless Playwright and Chromium or Chrome are available; it is
not part of the required gate. The same script can be run by hand: ``scripts/ui_smoke.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("playwright")


@pytest.mark.browser
@pytest.mark.git
def test_a_person_can_start_a_demo_run_and_open_its_diff() -> None:
    script = Path(__file__).parent.parent / "scripts" / "ui_smoke.py"

    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=420, check=False
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok" in done.stdout or "skipped" in done.stdout
