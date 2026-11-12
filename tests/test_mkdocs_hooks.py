"""``scripts/mkdocs_hooks.py``: links that leave ``docs/`` point at the repository and the
examples' run reports join the site. Skipped without the ``site`` dependency group (CI's ``site``
job builds the real site with ``mkdocs build --strict``)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

pytest.importorskip("mkdocs")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mkdocs_hooks.py"


@pytest.fixture
def hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("mkdocs_hooks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "docs").mkdir()
    (tmp_path / "CONTRIBUTING.md").write_text("# c\n", encoding="utf-8")
    (tmp_path / "examples" / "demo").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def page_in(root: Path, name: str = "page.md") -> tuple[SimpleNamespace, dict[str, str]]:
    return SimpleNamespace(file=SimpleNamespace(abs_src_path=str(root / "docs" / name))), {
        "docs_dir": str(root / "docs")
    }


def test_a_link_out_of_docs_becomes_a_link_to_the_repository(
    hooks: ModuleType, tmp_path: Path
) -> None:
    page, config = page_in(tmp_path)
    text = (
        '[c](../CONTRIBUTING.md#the-website) [d](../examples/) [t](../CONTRIBUTING.md "Title") '
        "[w](https://example.com/x) [a](#here) [inside](other.md) [gone](../NOPE.md)"
    )

    out = hooks.on_page_markdown(text, page, config, None)

    base = hooks.REPOSITORY
    assert f"[c]({base}/blob/main/CONTRIBUTING.md#the-website)" in out
    assert f"[d]({base}/tree/main/examples)" in out
    assert f'[t]({base}/blob/main/CONTRIBUTING.md "Title")' in out
    assert "[w](https://example.com/x) [a](#here) [inside](other.md) [gone](../NOPE.md)" in out


def test_each_examples_reports_are_published_under_reports(
    hooks: ModuleType, tmp_path: Path
) -> None:
    demo = tmp_path / "examples" / "demo"
    (demo / "report.html").write_text("<html>", encoding="utf-8")
    (demo / "report-live.html").write_text("<html>", encoding="utf-8")

    assert sorted(hooks.example_reports()) == ["reports/demo-live.html", "reports/demo.html"]
