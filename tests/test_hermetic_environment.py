from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TEST_API_KEY = "test-key-not-real"
ROOT = Path(__file__).resolve().parents[1]

POLLUTED_ENVIRONMENT = {
    "ENGINEERING_RUN_PROFILE": "smoke",
    "ENGINEERING_LEAD_MODEL": "polluted/lead",
    "ENGINEERING_WORKSPACE_ROOT": "/polluted/workspace",
    "OPENAI_API_KEY": "real-looking-key",
}


def test_environment_is_isolated(hermetic_environment: Path) -> None:
    assert not [name for name in os.environ if name.startswith("ENGINEERING_")]
    assert os.environ["OPENAI_API_KEY"] == TEST_API_KEY
    assert Path.cwd() == hermetic_environment
    assert Path(os.environ["CREWAI_STORAGE_DIR"]).is_relative_to(hermetic_environment)
    assert Path(os.environ["HOME"]).is_relative_to(hermetic_environment)


@pytest.mark.skipif(
    os.environ.get("ENGINEERING_TEST_NESTED") == "1",
    reason="avoid recursive pytest invocation",
)
def test_polluted_parent_environment_cannot_leak_into_tests() -> None:
    environment = {**os.environ, **POLLUTED_ENVIRONMENT, "ENGINEERING_TEST_NESTED": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{Path(__file__)}::test_environment_is_isolated",
        ],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
