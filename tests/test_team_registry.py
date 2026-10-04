"""The team roster: built-ins, overrides, custom teammates, fallback, and the commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pipeline_fakes import PLAN
from test_pipeline_crews import run_cli, scripts, write
from test_pipeline_flow import only_run, run_dir
from typer.testing import CliRunner

from engineering_team import main
from engineering_team.board.store import BoardStore
from engineering_team.cli.app import app
from engineering_team.contracts import WorkPackage
from engineering_team.crew import EngineeringTeam
from engineering_team.pipeline import strategies
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.pipeline.stages import CrewStageRunner, package_pool, teammate_for
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import Settings, SettingsError, load_settings
from engineering_team.team import TeamError, build_roster, builtin_roster
from engineering_team.team.registry import DEFAULT_TOOL_GROUPS, VALID_GROUPS
from engineering_team.testing import ScriptedLLM

BUILTINS = {
    "engineering_lead",
    "product_analyst",
    "solution_architect",
    "backend_engineer",
    "frontend_engineer",
    "quality_engineer",
    "generalist_engineer",
    "code_reviewer",
    "security_engineer",
    "devops_engineer",
    "technical_writer",
    "debugger",
    "codebase_analyst",
}
DATA_ENGINEER = """\
data_engineer:
  role: Data engineer for {project_name}
  goal: Build the data layer.
  backstory: You love tables.
  tool_groups: [fs_read, fs_write, search, command, board, notes]
  stages: [implement]
"""
runner = CliRunner()


def settings_with(**team: dict[str, Any]) -> Settings:
    return load_settings().with_overrides({"team": team}, source="test")


def team_file(text: str, name: str = ".engineering-team/team.yaml") -> Path:
    path = Path.cwd() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- the built-ins ------------------------------------------------------------------------------


def test_the_built_in_teammates_are_valid_and_come_from_agents_yaml() -> None:
    roster = builtin_roster()

    assert set(roster.members) == BUILTINS
    for member in roster.all():
        assert member.builtin and member.enabled and member.role and member.goal
        assert set(member.tool_groups) <= set(VALID_GROUPS), member.key
    assert roster.get("engineering_lead").groups == ()
    assert roster.get("engineering_lead").allow_delegation is True


def test_the_architect_plans_on_the_reviewer_tier() -> None:
    """Measured in docs/BENCHMARKS.md: a worker-tier architect broke the plan rules too often."""

    roster = builtin_roster()
    assert roster.get("solution_architect").tier == "reviewer"
    assert roster.get("backend_engineer").tier == "worker"  # volume work stays on the cheap model


def test_default_tool_groups_follow_the_roles() -> None:
    roster = builtin_roster()
    writers = {"fs_write", "command"}

    analyst = set(roster.get("product_analyst").groups)
    assert not analyst & writers and {"fs_read", "search", "knowledge", "human"} <= analyst
    assert "fs_write" in roster.get("solution_architect").groups  # it writes docs/architecture.md
    backend, frontend = roster.get("backend_engineer"), roster.get("frontend_engineer")
    assert {"fs_write", "command", "dev", "runtime", "code_intel"} <= set(backend.groups)
    assert "browser" not in backend.groups and "browser" in frontend.groups
    assert "browser" in roster.get("quality_engineer").groups
    assert set(roster.get("generalist_engineer").groups) >= set(backend.groups)
    cartographer = set(roster.get("codebase_analyst").groups)  # reads the code, never changes it
    assert (
        not cartographer & writers
        and {"fs_read", "search", "code_intel", "git_read"} <= cartographer
    )


# -- overrides ----------------------------------------------------------------------------------


def test_overrides_change_only_the_fields_they_set_and_the_config_beats_the_team_file() -> None:
    team_file("backend_engineer:\n  goal: From the team file.\n  tier: cheap\n  max_iter: 7\n")
    settings = settings_with(backend_engineer={"tier": "reviewer", "enabled": True})

    member = build_roster(settings).get("backend_engineer")

    assert member.goal == "From the team file."  # the file's, nothing in config set it
    assert member.tier == "reviewer"  # the config beat the file
    assert member.max_iter == 7
    assert member.role == builtin_roster().get("backend_engineer").role  # untouched
    assert member.origin == "built-in + .engineering-team/team.yaml + config"


def test_the_team_file_may_wrap_the_teammates_in_a_team_key_and_may_be_elsewhere() -> None:
    path = team_file("team:\n  backend_engineer:\n    goal: Wrapped.\n", name="custom/team.yaml")
    settings = load_settings().with_overrides(
        {"team_file": str(path.relative_to(Path.cwd()))}, source="t"
    )

    assert build_roster(settings).get("backend_engineer").goal == "Wrapped."


def test_a_new_teammate_is_added_without_python_and_is_read_only_by_default() -> None:
    team_file(DATA_ENGINEER)
    settings = settings_with(
        analyst_two={"role": "Second analyst", "goal": "Analyse.", "backstory": "Careful."}
    )

    roster = build_roster(settings)

    assert len(roster.members) == len(BUILTINS) + 2
    data = roster.get("data_engineer")
    assert not data.builtin and data.origin == "custom (.engineering-team/team.yaml)"
    assert data.stages == ("implement",) and "fs_write" in data.groups
    assert roster.get("analyst_two").tool_groups == DEFAULT_TOOL_GROUPS  # nothing that writes
    assert not {"fs_write", "command"} & set(roster.get("analyst_two").groups)


def test_a_built_in_can_be_disabled_and_a_prompt_changed_from_the_config_file() -> None:
    (Path.cwd() / "engineering-team.toml").write_text(
        "[team.frontend_engineer]\nenabled = false\n\n"
        '[team.quality_engineer]\ngoal = "Break it."\n',
        encoding="utf-8",
    )

    roster = build_roster(load_settings(config_file="engineering-team.toml"))

    assert not roster.get("frontend_engineer").enabled
    assert roster.get("quality_engineer").goal == "Break it."


# -- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("team", "message"),
    [
        ({"data_engineer": {"role": "Only a role"}}, r"'data_engineer' is new.*goal, backstory"),
        (
            {"backend_engineer": {"tool_groups": ["fs_read", "telepathy"]}},
            r"'backend_engineer'.*unknown tool group\(s\): telepathy.*Known: fs_read",
        ),
        ({"backend_engineer": {"modes": ["rewrite"]}}, r"unknown mode\(s\): rewrite.*new, feature"),
        ({"backend_engineer": {"stages": ["Not A Stage"]}}, r"invalid stage name"),
    ],
)
def test_invalid_team_definitions_are_rejected_with_the_teammate_named(
    team: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_roster(settings_with(**team))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"tier": "genius"}, r"team\.backend_engineer\.tier.*'lead', 'worker', 'cheap'"),
        ({"max_iter": 0}, r"team\.backend_engineer\.max_iter"),
        ({"colour": "red"}, r"Unknown setting 'team\.backend_engineer\.colour'"),
    ],
)
def test_the_schema_rejects_bad_values_and_unknown_fields_when_settings_load(
    fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        settings_with(backend_engineer=fields)


def test_bad_team_keys_and_files_are_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="lowercase letters, digits"):
        settings_with(**{"Data Engineer": {"role": "x"}})
    team_file("- not\n- a mapping\n")
    with pytest.raises(TeamError, match="must map each teammate key"):
        build_roster(load_settings())
    team_file("data_engineer: [broken")
    with pytest.raises(TeamError, match="not valid YAML"):
        build_roster(load_settings())
    missing = load_settings().with_overrides({"team_file": "nowhere.yaml"}, source="t")
    with pytest.raises(TeamError, match=r"nowhere\.yaml.*does not exist"):
        build_roster(missing)


def test_an_invalid_team_stops_a_run_before_it_starts(capsys: pytest.CaptureFixture[str]) -> None:
    team_file("backend_engineer:\n  tool_groups: [telepathy]\n")

    code = main.run(
        ["new", "--request", "Build a thing", "--prepare-only", "--workspace-root", "ws"]
    )

    assert code == 2 and "unknown tool group(s): telepathy" in capsys.readouterr().err
    assert not (Path.cwd() / "ws" / "mvp-app" / ".engineering-team" / "runs").exists()


# -- who works when someone is missing --------------------------------------------------------


def test_a_disabled_teammate_falls_back_to_the_nearest_enabled_generalist() -> None:
    roster = build_roster(settings_with(backend_engineer={"enabled": False}))

    assert roster.assign(["backend_engineer"], "foundation") == (
        "generalist_engineer",
        "teammate 'backend_engineer' is disabled",
    )
    assert roster.assign(["backend_engineer", "quality_engineer"], "x")[0] == "quality_engineer"
    assert roster.assign(["product_analyst"], "spec") == ("product_analyst", "")


def test_without_a_generalist_the_teammate_with_the_most_similar_tools_steps_in() -> None:
    roster = build_roster(
        settings_with(backend_engineer={"enabled": False}, generalist_engineer={"enabled": False})
    )

    key, why = roster.assign(["backend_engineer"], "foundation")

    assert key in {"frontend_engineer", "quality_engineer"} and "disabled" in why
    assert roster.assign(["backend_engineer"], "foundation")[0] == key  # deterministic


def test_a_stage_nobody_can_work_is_an_error_that_says_how_to_fix_it() -> None:
    off = {"enabled": False}
    roster = build_roster(
        settings_with(
            backend_engineer=off,
            frontend_engineer=off,
            quality_engineer=off,
            generalist_engineer=off,
            solution_architect=off,
            devops_engineer=off,
            debugger=off,
        )  # fmt: skip
    )

    with pytest.raises(TeamError, match="No enabled teammate can work the 'foundation' stage"):
        roster.assign(["backend_engineer"], "foundation")


def test_a_teammate_limited_to_other_modes_is_not_used_in_this_one() -> None:
    roster = build_roster(settings_with(backend_engineer={"modes": ["fix"]}))

    assert roster.assign(["backend_engineer"], "implement", "new")[0] == "generalist_engineer"
    assert roster.assign(["backend_engineer"], "implement", "fix")[0] == "backend_engineer"


def test_work_packages_go_to_the_teammate_the_role_names_including_custom_ones() -> None:
    stage = load_recipe("new").stage("implement")
    team_file(DATA_ENGINEER)
    roster = build_roster(load_settings())

    def role(name: str, team: Any = roster) -> str:
        return teammate_for(stage, WorkPackage(id="WP", title="t", role=name), team)

    assert role("backend") == "backend_engineer" and role("front end") == "frontend_engineer"
    assert role("data_engineer") == "data_engineer" and role("Data") == "data_engineer"
    assert role("devops") == "backend_engineer"  # nobody by that name: the first listed
    assert package_pool(stage, roster) == ["backend_engineer", "frontend_engineer", "data_engineer"]
    off = build_roster(settings_with(frontend_engineer={"enabled": False}))
    assert role("frontend", off) == "backend_engineer"  # disabled: the nearest enabled one
    assert package_pool(stage, off) == ["backend_engineer", "data_engineer"]


def test_a_custom_teammate_that_does_not_list_the_stage_is_never_picked_by_role() -> None:
    stage = load_recipe("new").stage("implement")
    roster = build_roster(settings_with(data_engineer={"role": "D", "goal": "g", "backstory": "b"}))

    package = WorkPackage(id="WP", title="t", role="data_engineer")

    assert teammate_for(stage, package, roster) == "backend_engineer"


# -- models -----------------------------------------------------------------------------------


def test_a_teammates_tier_and_iteration_limit_choose_its_model() -> None:
    standard = load_settings(env={"ENGINEERING_PROVIDER": "openai"})
    worker = standard.resolve_model("x", tier="worker")
    reviewer = standard.resolve_model("x", tier="reviewer", max_iter=11)
    lead = standard.resolve_model("x", tier="lead")

    assert (worker.tier, reviewer.tier, lead.tier) == ("worker", "reviewer", "lead")
    assert reviewer.max_iter == 11 and reviewer.sources["max_iter"] == "team setting"
    assert lead.slot == "lead" and worker.slot == "worker"
    assert standard.resolve_model("engineering_lead").tier == "lead"  # unchanged without a tier


def test_the_smoke_profile_keeps_its_own_models_whatever_the_tier() -> None:
    smoke = load_settings(env={"ENGINEERING_RUN_PROFILE": "smoke"})

    assert smoke.resolve_model("x", tier="reviewer").model == smoke.resolve_model("x").model
    assert smoke.resolve_model("x", tier="lead").slot == "lead"


def test_models_roles_still_overrides_a_teammates_tier() -> None:
    settings = load_settings().with_overrides(
        {"models": {"roles": {"x": {"model": "openai/custom-model", "max_iter": 3}}}}, source="t"
    )

    resolved = settings.resolve_model("x", tier="reviewer", max_iter=9)

    assert resolved.model == "openai/custom-model" and resolved.max_iter == 3


# -- a run uses the roster ------------------------------------------------------------------------


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, ScriptedLLM]], None]:
    def use(llms: dict[str, ScriptedLLM]) -> None:
        monkeypatch.setattr(
            strategies,
            "CrewStageRunner",
            lambda: CrewStageRunner(llm_factory=lambda key: llms[key]),
        )

    return use


def test_a_custom_teammate_from_a_project_yaml_works_a_package_in_a_scripted_run(
    install: Callable[[dict[str, ScriptedLLM]], None],
) -> None:
    team_file(DATA_ENGINEER)
    plan = PLAN.model_copy(
        update={
            "work_packages": [
                PLAN.work_packages[0].model_copy(update={"role": "data_engineer"}),
                PLAN.work_packages[1],
            ]
        }
    )
    llms = scripts()
    llms["solution_architect"] = ScriptedLLM(
        [write("docs/architecture.md"), plan.model_dump_json()]
    )
    llms["backend_engineer"] = ScriptedLLM(
        [
            write("README.md"),
            "Foundation is in place.",
            write("src/cli/main.py", "VALUE = 2\n"),
            "WP-2 delivered AC-2.",
            write("docs/integration.md"),
            "Integrated.",
        ]
    )
    llms["data_engineer"] = ScriptedLLM(
        [write("src/storage/main.py", "VALUE = 1\n"), "WP-1 delivered AC-1."]
    )
    install(llms)

    assert run_cli() == 0

    for llm in llms.values():
        llm.assert_exhausted()
    first = llms["data_engineer"].calls[0]
    assert first.tools is not None
    names = {tool["function"]["name"] for tool in first.tools}
    assert {"write_project_file", "run_project_command", "write_note"} <= names
    assert not {"browser_open", "web_search", "find_symbol"} & names  # only what it was given
    assert "Data engineer for demo" in first.prompt and "Build the data layer." in first.prompt
    plan_prompt = llms["solution_architect"].calls[0].prompt
    assert "- data_engineer: Data engineer for demo" in plan_prompt
    cards = {
        c.title: c.assignee for c in BoardStore(run_dir(only_run())).cards(kind="work_package")
    }
    assert cards == {"WP-1: Storage": "data_engineer", "WP-2: CLI": "backend_engineer"}


def test_a_disabled_analyst_is_replaced_by_the_generalist_and_the_run_says_so(
    install: Callable[[dict[str, ScriptedLLM]], None],
) -> None:
    config = team_file("[team.product_analyst]\nenabled = false\n", name="team.toml")
    llms = scripts()
    llms["generalist_engineer"] = llms.pop("product_analyst")
    install(llms)

    assert run_cli("--config", str(config)) == 0

    llms["generalist_engineer"].assert_exhausted()
    fallbacks = [
        e.data
        for e in read_events(run_dir(only_run()) / "events.jsonl")
        if e.type == "team.fallback"
    ]
    assert fallbacks == [
        {
            "wanted": "product_analyst",
            "used": "generalist_engineer",
            "why": "teammate 'product_analyst' is disabled",
        }
    ]
    spec_card = BoardStore(run_dir(only_run())).cards(kind="stage")[0]
    assert spec_card.assignee == "generalist_engineer"


def test_the_hierarchical_crew_still_builds_and_takes_prompt_and_tier_changes(
    make_context: Callable[..., RunContext],
) -> None:
    settings = settings_with(
        solution_architect={"goal": "Ship the smallest thing.", "tier": "cheap"}
    )
    ctx = make_context("hier", settings=settings)

    crew = EngineeringTeam(ctx).crew()

    assert len(crew.agents) == 4 and crew.manager_agent is not None
    architect = next(a for a in crew.agents if "architect" in a.role.lower())
    assert architect.goal == "Ship the smallest thing."
    assert architect.tools  # still the project tools


# -- the commands ------------------------------------------------------------------------------


def test_team_lists_everyone_with_source_and_state_and_json_has_the_detail() -> None:
    team_file(DATA_ENGINEER)
    settings_file = team_file("[team.frontend_engineer]\nenabled = false\n", name="c.toml")

    plain = runner.invoke(app, ["team", "list", "--config", str(settings_file)])
    rows = json.loads(
        runner.invoke(app, ["--json", "team", "list", "--config", str(settings_file)]).stdout
    )

    assert plain.exit_code == 0
    assert "data_engineer" in plain.stdout and "disabled" in plain.stdout
    by_key = {row["key"]: row for row in rows}
    assert set(by_key) == BUILTINS | {"data_engineer"}
    assert by_key["data_engineer"]["origin"] == "custom (.engineering-team/team.yaml)"
    assert by_key["frontend_engineer"]["enabled"] is False
    assert "{project_name}" not in by_key["data_engineer"]["role"]
    assert runner.invoke(app, ["team"]).stdout == runner.invoke(app, ["team", "list"]).stdout


def test_team_show_prints_one_teammate_in_full_and_unknown_keys_are_usage_errors() -> None:
    shown = runner.invoke(app, ["team", "show", "quality_engineer"])
    data = json.loads(runner.invoke(app, ["--json", "team", "show", "backend_engineer"]).stdout)
    unknown = runner.invoke(app, ["team", "show", "nobody"])

    assert shown.exit_code == 0
    for label in ("Role:", "Goal:", "Backstory:", "Model:", "Tool groups:", "Source:"):
        assert label in shown.stdout
    assert data["key"] == "backend_engineer" and "fs_write" in data["tool_groups"]
    assert unknown.exit_code == 2 and "Unknown teammate 'nobody'" in unknown.stderr


def test_team_commands_report_an_invalid_definition_as_a_usage_error() -> None:
    team_file("backend_engineer:\n  tool_groups: [telepathy]\n")

    result = runner.invoke(app, ["team", "list"])

    assert result.exit_code == 2 and "telepathy" in result.stderr


def test_doctor_checks_the_team_and_names_missing_tools() -> None:
    from engineering_team.cli.doctor import run_checks

    healthy = next(c for c in run_checks(load_settings()) if c.name == "Team")
    team_file("backend_engineer:\n  tool_groups: [telepathy]\n")
    broken = next(c for c in run_checks(load_settings()) if c.name == "Team")

    assert healthy.status in {"ok", "warn"} and "13 of 13 teammates enabled" in healthy.detail
    assert broken.status == "fail" and "telepathy" in broken.detail


def test_every_default_teammate_is_documented_in_team_md() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "TEAM.md").read_text(encoding="utf-8")

    assert [key for key in BUILTINS if f"`{key}`" not in doc] == []
    from engineering_team.settings import TeamOverride

    assert [f for f in TeamOverride.model_fields if f"`{f}`" not in doc] == []
