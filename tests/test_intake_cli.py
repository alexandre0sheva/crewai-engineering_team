"""Requirements intake through the command line: sources, stdin, context documents, templates."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from pipeline_fakes import FakeRunner, write_checks
from test_pipeline_flow import ROOT, project, resume, runs, use_runner

from engineering_team import main
from engineering_team.cli.ask import TerminalAnswerer
from engineering_team.intake import RequestBundle, request_hash, template_for
from engineering_team.runtime.interaction import HumanChannel
from engineering_team.runtime.run_store import RunStore

TEXT = "Build a tiny notes CLI.\n\n- add stores a note\n- list prints notes\n"


def new(*extra: str, name: str = "demo") -> int:
    return main.run(
        [
            "new",
            "--project-name",
            name,
            "--workspace-root",
            str(Path.cwd() / ROOT),
            "--strategy",
            "pipeline",
            "--checks",
            str(write_checks()),
            *extra,
        ]
    )


def write(name: str, text: str) -> str:
    path = Path.cwd() / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def request_md(name: str = "demo") -> str:
    (manifest,) = runs(name)
    path = RunStore(project(name)).run_dir(manifest.run_id) / "request.md"
    return path.read_text(encoding="utf-8").rstrip("\n")


def docs_dir() -> str:
    docs = Path.cwd() / "refs"
    docs.mkdir(exist_ok=True)
    (docs / "domain.md").write_text("# Domain\nA note has a title and a body.\n", "utf-8")
    (docs / "style.txt").write_text("Prefer short commands.\n", "utf-8")
    return str(docs)


# -- the same text, the same hash ----------------------------------------------------------------


def test_the_same_request_text_has_the_same_hash_from_a_file_stdin_and_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())
    file = write("request.md", TEXT.replace("\n", "\r\n"))
    assert new("--request-file", file, name="from-file") == 0
    stdin(monkeypatch, TEXT)
    assert new("--request-file", "-", name="from-stdin") == 0
    stdin(monkeypatch, TEXT)
    assert new("--request", "-", name="from-stdin-inline") == 0
    assert new("--request", TEXT, name="from-text") == 0

    hashes = {
        name: runs(name)[0].request_hash
        for name in ("from-file", "from-stdin", "from-stdin-inline", "from-text")
    }

    assert set(hashes.values()) == {RequestBundle.from_sources(text=TEXT).hash}


def test_several_files_and_text_merge_in_order_and_resume_keeps_the_merged_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner(fail={"plan": 2}))
    first, second = write("a.md", "first part"), write("b.md", "second part")

    assert new("--request", "inline part", "--request-file", first, "--request-file", second) == 1

    merged = request_md()
    assert merged == (
        "## Request: inline request\n\ninline part\n\n"
        "## Request: a.md\n\nfirst part\n\n"
        "## Request: b.md\n\nsecond part"
    )
    (manifest,) = runs()
    assert manifest.request_hash == request_hash(merged)
    runner.fail.clear()

    assert resume(manifest.run_id) == 0  # no request given: the stored one is trusted

    (after,) = runs()
    assert after.status == "succeeded" and after.resumes == 1
    assert "## Request: a.md" in runner.requests[-1].requirements


# -- refusals are usage errors --------------------------------------------------------------------


def test_bad_requests_exit_2_with_one_line_and_start_no_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner())
    cases = [
        (["--request", "x" * 60_001], "limit is 60,000"),
        (["--request", "   "], "empty"),
        (["--request-file", str(Path.cwd() / "missing.md")], "missing.md"),
        (["--request", TEXT, "--example", "tiny-notes"], "Use only one of"),
        (["--example", "tiny-notes", "--request-file", write("r.md", TEXT)], "Use only one of"),
        (["--request", TEXT, "--context-dir", str(Path.cwd() / "nowhere")], "not a directory"),
    ]
    for arguments, message in cases:
        assert new(*arguments) == 2, arguments
        assert message in capsys.readouterr().err
    stdin(monkeypatch, "")
    assert new("--request-file", "-") == 2 and "empty" in capsys.readouterr().err
    assert runs() == []


def test_init_templates_cannot_be_run_unedited_and_each_mode_has_its_own(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner())
    for mode, heading in (
        ("new", "build something new"),
        ("feature", "add a feature"),
        ("fix", "fix a bug"),
        ("maintain", "maintain an existing project"),
    ):
        assert main.run(["init", "--yes", "--force", "--mode", mode]) == 0
        written = Path("PROJECT_REQUEST.md").read_text(encoding="utf-8")
        assert written == template_for(mode) and heading in written
        capsys.readouterr()
        assert new("--request-file", "PROJECT_REQUEST.md") == 2
        assert "still a template (PROJECT_REQUEST.md)" in capsys.readouterr().err
    assert main.run(["init", "--yes", "--force", "--mode", "rewrite"]) == 2


def test_init_defaults_to_the_new_template() -> None:
    assert main.run(["init", "--yes"]) == 0

    assert Path("PROJECT_REQUEST.md").read_text(encoding="utf-8") == template_for("new")


# -- context documents ---------------------------------------------------------------------------


def test_context_documents_are_copied_read_only_with_an_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, FakeRunner())

    assert new("--request", TEXT, "--context-dir", docs_dir()) == 0

    context = project() / ".engineering-team" / "context"
    assert (context / "domain.md").read_text().startswith("# Domain")
    assert "domain.md" in (context / "INDEX.md").read_text()
    assert not (context / "domain.md").stat().st_mode & 0o222  # read-only
    assert not (project() / "refs").exists()  # nothing leaked into the project itself


def test_context_limits_come_from_the_intake_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_runner(monkeypatch, FakeRunner())
    config = write("engineering-team.toml", "[intake]\nmax_context_files = 1\n")

    code = new("--request", TEXT, "--context-dir", docs_dir(), "--config", config)

    assert code == 2 and "max_context_files" in capsys.readouterr().err
    assert runs() == []


def test_prepare_only_copies_the_context_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, FakeRunner())

    assert new("--request", TEXT, "--context-dir", docs_dir(), "--prepare-only") == 0

    assert (project() / ".engineering-team" / "context" / "INDEX.md").is_file()
    assert runner.calls == []


# -- answering questions at the terminal -----------------------------------------------------------


def make_answerer(replies: list[str]) -> tuple[HumanChannel, TerminalAnswerer, list[str]]:
    from rich.console import Console

    human = HumanChannel()
    shown: list[str] = []
    answerer = TerminalAnswerer(
        human,
        Console(file=io.StringIO(), width=80),
        read=lambda prompt: replies.pop(0),
        pause=lambda: _Recorder(shown),
    )
    return human, answerer, shown


class _Recorder:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> None:
        self.log.append("paused")

    def __exit__(self, *exc: object) -> None:
        self.log.append("resumed")


def ask_in_thread(human: HumanChannel, text: str, out: list[str | None]) -> threading.Thread:
    thread = threading.Thread(target=lambda: out.append(human.ask(text, agent="product_analyst")))
    thread.start()
    return thread


def test_the_terminal_answerer_enables_the_channel_answers_and_pauses_the_screen() -> None:
    human, answerer, shown = make_answerer(["  a CLI  "])
    got: list[str | None] = []

    with answerer:
        assert human.interactive
        thread = ask_in_thread(human, "CLI or web?", got)
        thread.join(timeout=10)

    assert got == ["a CLI"] and shown == ["paused", "resumed"]


def test_an_empty_reply_declines_and_each_question_is_asked_once() -> None:
    human, answerer, shown = make_answerer(["", "yes"])
    got: list[str | None] = []

    with answerer:
        first = ask_in_thread(human, "One?", got)
        first.join(timeout=10)
        second = ask_in_thread(human, "Two?", got)
        second.join(timeout=10)

    assert got == [None, "yes"] and shown.count("paused") == 2
