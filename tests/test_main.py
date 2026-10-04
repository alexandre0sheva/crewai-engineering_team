from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering_team import main
from engineering_team.main import (
    SMOKE_PROFILE_MARKER,
    TEMPLATE_MARKER,
    available_examples,
    load_example,
    load_requirements,
    prepare_workspace,
    slugify_project_name,
)
from engineering_team.settings import load_settings

ENV_INLINE = "ENGINEERING_PROJECT_REQUEST"
ENV_FILE = "ENGINEERING_REQUEST_FILE"


def _patch_team(monkeypatch, team) -> None:
    """Replace the crew that ``run`` (hierarchical), ``train``, ``replay`` and ``test`` build."""

    from engineering_team.pipeline import strategies

    monkeypatch.setattr(main, "EngineeringTeam", team)
    monkeypatch.setattr(strategies, "EngineeringTeam", team)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_project_name_is_converted_to_a_safe_slug() -> None:
    assert slugify_project_name("  My Useful MVP!  ") == "my-useful-mvp"


def test_empty_project_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        slugify_project_name("---")


def test_concrete_request_file_is_loaded(tmp_path: Path) -> None:
    request = _write(tmp_path / "request.md", "# Build\nA small useful application.\n")

    assert load_requirements(request_file=request).startswith("# Build")


def test_placeholder_request_is_rejected(tmp_path: Path) -> None:
    request = _write(tmp_path / "request.md", f"{TEMPLATE_MARKER}\nReplace me")

    with pytest.raises(ValueError, match="still a template"):
        load_requirements(request_file=request)


def test_explicit_blank_request_is_an_error_not_a_fallback(monkeypatch) -> None:
    monkeypatch.setenv(ENV_INLINE, "from the environment")

    with pytest.raises(ValueError, match="empty"):
        load_requirements(inline_request="   ")


# --- request precedence (F3) -------------------------------------------------------------


@pytest.fixture
def all_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str | Path]:
    """Every request source populated with a distinct, recognisable text."""

    cli_file = _write(tmp_path / "cli.md", "cli file request")
    env_file = _write(tmp_path / "env.md", "env file request")
    _write(Path.cwd() / "PROJECT_REQUEST.md", "cwd default request")
    monkeypatch.setenv(ENV_INLINE, "env inline request")
    monkeypatch.setenv(ENV_FILE, str(env_file))
    return {"cli_file": cli_file}


def test_cli_inline_beats_the_environment(all_sources) -> None:
    assert load_requirements(inline_request="cli inline request") == "cli inline request"


def test_cli_inline_and_file_merge_in_order_ahead_of_the_environment(all_sources) -> None:
    result = load_requirements(
        inline_request="cli inline request", request_file=all_sources["cli_file"]
    )

    assert result == (
        "## Request: inline request\n\ncli inline request\n\n## Request: cli.md\n\ncli file request"
    )


def test_cli_file_beats_the_environment(all_sources) -> None:
    """Regression: the inline environment variable used to beat an explicit --request-file."""

    assert load_requirements(request_file=all_sources["cli_file"]) == "cli file request"


def test_cli_example_beats_the_environment(all_sources) -> None:
    assert "Tiny Notes CLI" in load_requirements(example="tiny-notes")


def test_environment_inline_beats_environment_file(all_sources) -> None:
    assert load_requirements() == "env inline request"


def test_environment_file_beats_the_cwd_default(all_sources, monkeypatch) -> None:
    monkeypatch.delenv(ENV_INLINE)

    assert load_requirements() == "env file request"


def test_cwd_default_file_is_the_last_resort(all_sources, monkeypatch) -> None:
    monkeypatch.delenv(ENV_INLINE)
    monkeypatch.delenv(ENV_FILE)

    assert load_requirements() == "cwd default request"


def test_blank_environment_values_count_as_unset(all_sources, monkeypatch) -> None:
    monkeypatch.setenv(ENV_INLINE, "  ")
    monkeypatch.setenv(ENV_FILE, "")

    assert load_requirements() == "cwd default request"


def test_missing_request_error_names_the_ways_to_provide_one() -> None:
    with pytest.raises(ValueError) as error:
        load_requirements()

    assert "--example tiny-notes" in str(error.value)
    assert "--request-file" in str(error.value)


def test_missing_explicit_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does-not-exist.md"):
        load_requirements(request_file=tmp_path / "does-not-exist.md")


def test_cli_rejects_combining_request_sources() -> None:
    with pytest.raises(SystemExit) as error:
        main._run_parser().parse_args(["--request", "x", "--example", "tiny-notes"])

    assert error.value.code == 2


# --- bundled examples and packaging (F4) -----------------------------------------------


def test_bundled_example_is_a_concrete_smoke_project() -> None:
    requirements = load_example("tiny-notes")

    assert available_examples() == ["tiny-notes"]
    assert "Tiny Notes CLI" in requirements
    assert SMOKE_PROFILE_MARKER in requirements
    assert load_settings().for_request(requirements).profile == "smoke"


def test_unknown_example_lists_the_available_ones() -> None:
    with pytest.raises(ValueError, match="tiny-notes"):
        load_example("nope")


def test_example_is_packaged_as_a_resource_not_found_relative_to_the_checkout() -> None:
    from importlib import resources

    resource = resources.files("engineering_team") / "examples" / "tiny-notes" / "request.md"

    assert resource.is_file()
    assert not hasattr(main, "PROJECT_ROOT")


def test_default_workspace_root_is_relative_to_the_callers_directory(tmp_path: Path) -> None:
    workspace = prepare_workspace("my-app")

    assert workspace.root == (Path.cwd() / "workspace" / "my-app").resolve()


def test_relative_workspace_root_resolves_against_cwd() -> None:
    workspace = prepare_workspace("my-app", "generated/apps")

    assert workspace.root == (Path.cwd() / "generated" / "apps" / "my-app").resolve()


def test_filesystem_root_cannot_be_a_workspace_root() -> None:
    root = Path(Path.cwd().anchor)

    with pytest.raises(ValueError, match="filesystem root"):
        prepare_workspace("unsafe", root)


# --- exit codes ------------------------------------------------------------------------


def test_prepare_only_succeeds_without_calling_the_crew(capsys, monkeypatch) -> None:
    _patch_team(monkeypatch, lambda: pytest.fail("crew must not be built"))

    code = main.run(["--example", "tiny-notes", "--project-name", "demo", "--prepare-only"])

    assert code == 0
    assert "Prepared project workspace" in capsys.readouterr().out


def test_missing_request_is_a_usage_error_without_a_traceback(capsys) -> None:
    code = main.run(["new", "--prepare-only"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.startswith("Error: No project request found")
    assert "Traceback" not in captured.err


def test_workspace_safety_errors_are_usage_errors(capsys, tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "demo").mkdir()
    (tmp_path / "workspace" / "demo" / "precious.txt").write_text("keep", encoding="utf-8")

    code = main.run(
        [
            "--request",
            "Build something concrete",
            "--project-name",
            "demo",
            "--workspace-root",
            str(tmp_path / "workspace"),
            "--prepare-only",
        ]
    )

    assert code == 2
    assert "was not created by engineering-team" in capsys.readouterr().err


@pytest.mark.usefixtures("crew_strategy")
def test_runtime_failure_exits_with_one_and_reports_the_cause(capsys, monkeypatch) -> None:
    class ExplodingTeam:
        def __init__(self, ctx=None) -> None:
            pass

        def crew(self):
            return SimpleNamespace(kickoff=self._boom)

        @staticmethod
        def _boom(inputs):
            raise RuntimeError("model unavailable")

    _patch_team(monkeypatch, ExplodingTeam)

    code = main.run(["--request", "Build a thing", "--project-name", "demo"])

    assert code == 1
    assert "model unavailable" in capsys.readouterr().err


def test_validation_errors_inside_the_crew_are_runtime_failures_not_usage_errors(
    monkeypatch,
) -> None:
    class ValidatingTeam:
        def __init__(self, ctx=None) -> None:
            pass

        def crew(self):
            return SimpleNamespace(kickoff=self._fail)

        @staticmethod
        def _fail(inputs):
            raise ValueError("raised deep inside the framework")

    _patch_team(monkeypatch, ValidatingTeam)

    assert main.run(["--request", "Build a thing", "--project-name", "demo"]) == 1


@pytest.mark.usefixtures("crew_strategy")
def test_keyboard_interrupt_exits_with_130(capsys, monkeypatch) -> None:
    class InterruptedTeam:
        def __init__(self, ctx=None) -> None:
            pass

        def crew(self):
            return SimpleNamespace(kickoff=self._interrupt)

        @staticmethod
        def _interrupt(inputs):
            raise KeyboardInterrupt

    _patch_team(monkeypatch, InterruptedTeam)

    assert main.run(["--request", "Build a thing", "--project-name", "demo"]) == 130
    assert "Interrupted" in capsys.readouterr().err


@pytest.mark.usefixtures("crew_strategy")
def test_successful_run_returns_zero_so_console_scripts_exit_cleanly(monkeypatch) -> None:
    """Regression: run() used to return the crew output, which ``sys.exit`` turns into 1."""

    calls = []

    class FakeTeam:
        def __init__(self, ctx=None) -> None:
            pass

        def crew(self):
            return SimpleNamespace(kickoff=lambda inputs: calls.append(inputs) or object())

    _patch_team(monkeypatch, FakeTeam)

    assert main.run(["--request", "Build a thing", "--project-name", "demo"]) == 0
    assert calls[0]["project_name"] == "demo"


@pytest.mark.parametrize(
    ("entry_point", "argv"),
    [
        (main.train, ["train"]),
        (main.replay, ["replay"]),
        (main.test, ["test"]),
        (main.run_with_trigger, ["run_with_trigger"]),
    ],
)
def test_crewai_entry_points_report_usage_errors_with_exit_code_two(
    entry_point, argv, monkeypatch
) -> None:
    monkeypatch.setattr("sys.argv", argv)

    assert entry_point() == 2


def test_trigger_payload_that_is_not_json_is_a_usage_error(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_with_trigger", "{not json"])

    assert main.run_with_trigger() == 2


# --- settings integration --------------------------------------------------------------


def test_settings_error_is_a_one_line_usage_error(capsys, monkeypatch) -> None:
    monkeypatch.setenv("ENGINEERING_PROVIDER", "nonsense")

    code = main.run(["new", "--example", "tiny-notes", "--prepare-only"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.count("\n") == 1
    assert "ENGINEERING_PROVIDER" in captured.err


def test_missing_credentials_fail_fast_before_any_model_call(capsys, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    _patch_team(monkeypatch, lambda *_: pytest.fail("crew must not be built"))

    code = main.run(["--request", "Build a thing", "--project-name", "demo"])

    assert code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_prepare_only_works_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    assert main.run(["--example", "tiny-notes", "--prepare-only"]) == 0


@pytest.mark.usefixtures("crew_strategy")
def test_cli_profile_and_environment_reach_the_crew_via_settings(monkeypatch) -> None:
    seen = []

    class FakeTeam:
        def __init__(self, ctx) -> None:
            seen.append(ctx.settings)

        def crew(self):
            return SimpleNamespace(kickoff=lambda inputs: None)

    _patch_team(monkeypatch, FakeTeam)
    monkeypatch.setenv("ENGINEERING_MAX_PARALLEL", "5")

    assert main.run(["--example", "tiny-notes", "--profile", "standard"]) == 0
    assert seen[0].profile == "standard"  # --profile beats the example's smoke marker
    assert seen[0].parallel.max_parallel_agents == 5
    assert "ENGINEERING_RUN_PROFILE" not in __import__("os").environ  # no env mutation


def test_environment_project_name_and_workspace_root_still_work(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENGINEERING_PROJECT_NAME", "From Env")
    monkeypatch.setenv("ENGINEERING_WORKSPACE_ROOT", str(tmp_path / "envroot"))

    assert main.run(["--example", "tiny-notes", "--prepare-only"]) == 0
    assert (tmp_path / "envroot" / "from-env" / ".engineering-team" / "owner.json").is_file()


def test_cli_project_name_beats_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENGINEERING_PROJECT_NAME", "from-env")

    main.run(["--example", "tiny-notes", "--project-name", "from-cli", "--prepare-only"])

    assert (Path.cwd() / "workspace" / "from-cli").is_dir()
    assert not (Path.cwd() / "workspace" / "from-env").exists()


def test_config_show_prints_values_and_sources(capsys, monkeypatch) -> None:
    monkeypatch.setenv("ENGINEERING_LEAD_MODEL", "openai/custom-lead-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-this-must-never-be-printed")

    code = main.run(["config", "show"])

    output = capsys.readouterr().out
    assert code == 0
    assert "openai/custom-lead-model" in output and "[env ENGINEERING_LEAD_MODEL]" in output
    assert "credentials.OPENAI_API_KEY" in output
    assert "sk-this-must-never-be-printed" not in output


def test_config_show_json_is_machine_readable(capsys) -> None:
    import json

    assert main.run(["config", "show", "--json", "--provider", "ollama"]) == 0

    rows = {row["key"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["provider"]["value"] == "ollama"
    assert rows["provider"]["source"] == "cli"


def test_config_show_reports_invalid_configuration_as_a_usage_error(capsys, monkeypatch) -> None:
    monkeypatch.setenv("ENGINEERING_MAX_PARALLEL", "many")

    assert main.run(["config", "show"]) == 2
    assert "ENGINEERING_MAX_PARALLEL" in capsys.readouterr().err


# --- workspace lock and run context --------------------------------------------------------


def test_a_workspace_in_use_by_another_run_is_a_usage_error(capsys, monkeypatch) -> None:
    from engineering_team.runtime.locks import WorkspaceLock

    _patch_team(monkeypatch, lambda *_: pytest.fail("crew must not be built"))
    argv = ["--request", "Build a thing", "--project-name", "demo"]
    assert main.run([*argv, "--prepare-only"]) == 0
    holder = WorkspaceLock(Path.cwd() / "workspace" / "demo").acquire("other-run")

    code = main.run(argv)

    holder.release()
    captured = capsys.readouterr()
    assert code == 2
    assert "in use by run other-run" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.usefixtures("crew_strategy")
def test_the_run_context_points_at_a_fresh_run_directory(monkeypatch) -> None:
    contexts = []

    class FakeTeam:
        def __init__(self, ctx) -> None:
            contexts.append(ctx)

        def crew(self):
            return SimpleNamespace(kickoff=lambda inputs: None)

    _patch_team(monkeypatch, FakeTeam)

    assert main.run(["--request", "Build a thing", "--project-name", "demo"]) == 0
    assert main.run(["--request", "Build a thing", "--project-name", "demo"]) == 0

    first, second = contexts
    assert first.run_id != second.run_id
    assert first.run_dir.parent == first.workspace.root / ".engineering-team" / "runs"
    assert first.run_dir.is_dir() and second.run_dir.is_dir()


@pytest.mark.parametrize("failure", [None, RuntimeError("model unavailable"), KeyboardInterrupt()])
def test_the_workspace_lock_is_released_however_the_run_ends(monkeypatch, failure) -> None:
    from engineering_team.runtime.locks import WorkspaceLock

    def kickoff(inputs):
        if failure is not None:
            raise failure

    class FakeTeam:
        def __init__(self, ctx=None) -> None:
            pass

        def crew(self):
            return SimpleNamespace(kickoff=kickoff)

    _patch_team(monkeypatch, FakeTeam)

    main.run(["--request", "Build a thing", "--project-name", "demo"])

    WorkspaceLock(Path.cwd() / "workspace" / "demo").acquire("next-run").release()
