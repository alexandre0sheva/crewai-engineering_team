from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.main import (
    DEFAULT_REQUEST_FILE,
    SMOKE_PROFILE_MARKER,
    TEMPLATE_MARKER,
    load_requirements,
    prepare_workspace,
    resolve_run_profile,
    slugify_project_name,
)


def test_project_name_is_converted_to_a_safe_slug() -> None:
    assert slugify_project_name("  My Useful MVP!  ") == "my-useful-mvp"


def test_empty_project_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        slugify_project_name("---")


def test_concrete_request_file_is_loaded(tmp_path: Path) -> None:
    request = tmp_path / "request.md"
    request.write_text("# Build\nA small useful application.\n", encoding="utf-8")

    assert load_requirements(request_file=request).startswith("# Build")


def test_placeholder_request_is_rejected(tmp_path: Path) -> None:
    request = tmp_path / "request.md"
    request.write_text(f"{TEMPLATE_MARKER}\nReplace me", encoding="utf-8")

    with pytest.raises(ValueError, match="still a template"):
        load_requirements(request_file=request)


def test_bundled_request_is_a_concrete_smoke_project() -> None:
    requirements = load_requirements(request_file=DEFAULT_REQUEST_FILE)

    assert "Tiny Notes CLI" in requirements
    assert SMOKE_PROFILE_MARKER in requirements
    assert resolve_run_profile(requirements) == "smoke"


def test_explicit_profile_overrides_request_marker() -> None:
    assert resolve_run_profile(SMOKE_PROFILE_MARKER, "standard") == "standard"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown engineering run profile"):
        resolve_run_profile("Concrete request", "expensive")


def test_filesystem_root_cannot_be_a_workspace_root() -> None:
    root = Path(Path.cwd().anchor)

    with pytest.raises(ValueError, match="filesystem root"):
        prepare_workspace("unsafe", root)
