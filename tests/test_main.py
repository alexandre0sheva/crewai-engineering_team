from __future__ import annotations

from pathlib import Path

import pytest
from pipeline_fakes import FakeRunner

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


def _patch_runner(monkeypatch, runner: FakeRunner | None = None) -> FakeRunner:
    """Replace the model-driven stage runner of every strategy with a scripted one."""

    from engineering_team.pipeline import strategies

    scripted = runner or FakeRunner()
    monkeypatch.setattr(strategies, "CrewStageRunner", lambda: scripted)
    return scripted


def _no_model_call(request) -> None:
    pytest.fail("no stage may run")


def _raise(error: BaseException):
    def on_call(request) -> None:
        raise error

    return on_call


RUN = ["new", "--request", "Build a thing", "--project-name", "demo", "--strategy", "single"]


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


def test_prepare_only_succeeds_without_running_a_stage(capsys, monkeypatch) -> None:
    _patch_runner(monkeypatch, FakeRunner(on_call=_no_model_call))

    code = main.run(["new", "--example", "tiny-notes", "--project-name", "demo", "--prepare-only"])

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
            "new",
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


def test_runtime_failure_exits_with_one_and_reports_the_cause(capsys, monkeypatch) -> None:
    _patch_runner(monkeypatch, FakeRunner(on_call=_raise(RuntimeError("model unavailable"))))

    code = main.run(RUN)

    assert code == 1
    assert "model unavailable" in capsys.readouterr().err


def test_validation_errors_inside_a_stage_are_runtime_failures_not_usage_errors(
    monkeypatch,
) -> None:
    _patch_runner(
        monkeypatch, FakeRunner(on_call=_raise(ValueError("raised deep inside the framework")))
    )

    assert main.run(RUN) == 1


def test_keyboard_interrupt_exits_with_130(capsys, monkeypatch) -> None:
    _patch_runner(monkeypatch, FakeRunner(on_call=_raise(KeyboardInterrupt())))

    assert main.run(RUN) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_successful_run_returns_zero_so_console_scripts_exit_cleanly(monkeypatch) -> None:
    """Regression: run() used to return the crew output, which ``sys.exit`` turns into 1."""

    runner = _patch_runner(monkeypatch)

    assert main.run(RUN) == 0
    assert runner.requests[0].ctx.settings.project_name == "demo"


# --- the 0.1.0 surface is gone ----------------------------------------------------------


@pytest.mark.parametrize("argv", [["--request-file", "REQUEST.md"], ["--request", "x"]])
def test_the_bare_invocation_points_at_new_and_exits_two(argv, capsys) -> None:
    code = main.run(argv)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.count("\n") == 1
    assert "engineering-team new --request-file" in captured.err
    assert "Traceback" not in captured.err


def test_a_project_request_file_alone_does_not_start_a_run(capsys) -> None:
    (Path.cwd() / "PROJECT_REQUEST.md").write_text("Build something concrete.", encoding="utf-8")

    assert main.run([]) == 2
    assert "engineering-team new" in capsys.readouterr().err


def test_no_arguments_and_no_request_shows_the_help(capsys) -> None:
    assert main.run([]) == 0
    assert "Usage:" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["train", "replay", "test", "run_with_trigger"])
def test_the_crewai_training_entry_points_are_gone(name) -> None:
    assert not hasattr(main, name)


def test_the_hierarchical_crew_module_is_gone() -> None:
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("engineering_team.crew")


def test_the_hierarchical_strategy_is_a_usage_error_naming_the_valid_ones(capsys) -> None:
    code = main.run([*RUN, "--strategy", "hierarchical"])

    assert code == 2
    err = capsys.readouterr().err
    assert "hierarchical" in err and "pipeline" in err and "single" in err


def test_run_crew_runs_new_with_the_command_line_options(monkeypatch) -> None:
    runner = _patch_runner(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["run_crew", *RUN[1:]]
    )  # what `crewai run` starts: no command, options only

    assert main.run_crew() == 0
    assert runner.calls


def test_run_crew_reads_the_default_request_file(monkeypatch) -> None:
    (Path.cwd() / "PROJECT_REQUEST.md").write_text("Build something concrete.", encoding="utf-8")
    runner = _patch_runner(monkeypatch)
    monkeypatch.setattr("sys.argv", ["run_crew", "--strategy", "single"])

    assert main.run_crew() == 0
    assert runner.calls


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
    _patch_runner(monkeypatch, FakeRunner(on_call=_no_model_call))

    code = main.run(RUN)

    assert code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_prepare_only_works_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    assert main.run(["new", "--example", "tiny-notes", "--prepare-only"]) == 0


def test_cli_profile_and_environment_reach_the_stages_via_settings(monkeypatch) -> None:
    runner = _patch_runner(monkeypatch)
    monkeypatch.setenv("ENGINEERING_MAX_PARALLEL", "5")

    code = main.run(
        ["new", "--example", "tiny-notes", "--profile", "standard", "--strategy", "single"]
    )

    settings = runner.requests[0].ctx.settings
    assert code == 0
    assert settings.profile == "standard"  # --profile beats the example's smoke marker
    assert settings.parallel.max_parallel_agents == 5
    assert "ENGINEERING_RUN_PROFILE" not in __import__("os").environ  # no env mutation


def test_environment_project_name_and_workspace_root_still_work(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENGINEERING_PROJECT_NAME", "From Env")
    monkeypatch.setenv("ENGINEERING_WORKSPACE_ROOT", str(tmp_path / "envroot"))

    assert main.run(["new", "--example", "tiny-notes", "--prepare-only"]) == 0
    assert (tmp_path / "envroot" / "from-env" / ".engineering-team" / "owner.json").is_file()


def test_cli_project_name_beats_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENGINEERING_PROJECT_NAME", "from-env")

    main.run(["new", "--example", "tiny-notes", "--project-name", "from-cli", "--prepare-only"])

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

    _patch_runner(monkeypatch, FakeRunner(on_call=_no_model_call))
    argv = RUN
    assert main.run([*argv, "--prepare-only"]) == 0
    holder = WorkspaceLock(Path.cwd() / "workspace" / "demo").acquire("other-run")

    code = main.run(argv)

    holder.release()
    captured = capsys.readouterr()
    assert code == 2
    assert "in use by run other-run" in captured.err
    assert "Traceback" not in captured.err


def test_the_run_context_points_at_a_fresh_run_directory(monkeypatch) -> None:
    runner = _patch_runner(monkeypatch)

    assert main.run(RUN) == 0
    assert main.run(RUN) == 0

    first, second = (request.ctx for request in runner.requests)
    assert first.run_id != second.run_id
    assert first.run_dir.parent == first.workspace.root / ".engineering-team" / "runs"
    assert first.run_dir.is_dir() and second.run_dir.is_dir()


@pytest.mark.parametrize("failure", [None, RuntimeError("model unavailable"), KeyboardInterrupt()])
def test_the_workspace_lock_is_released_however_the_run_ends(monkeypatch, failure) -> None:
    from engineering_team.runtime.locks import WorkspaceLock

    _patch_runner(monkeypatch, FakeRunner(on_call=_raise(failure) if failure else None))

    main.run(RUN)

    WorkspaceLock(Path.cwd() / "workspace" / "demo").acquire("next-run").release()
