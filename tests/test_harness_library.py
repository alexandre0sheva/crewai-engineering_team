"""The harness library (T18): domains, packs, overlays, skills, Load Skill, the run record."""

from __future__ import annotations

import dataclasses
import json
import os
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest
from test_pipeline_crews import run_cli, scripts
from test_pipeline_flow import only_run, run_dir
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import TaskProfile
from engineering_team.harness import HarnessError
from engineering_team.harness.domains import ANY, Domain, domain_for
from engineering_team.harness.library import (
    recorded_domain,
    resolve,
    resolve_for_run,
    sources,
)
from engineering_team.pipeline import strategies
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.pipeline.stages import CrewStageRunner
from engineering_team.report.collect import build_report
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import load_settings
from engineering_team.team import build_roster
from engineering_team.testing import ScriptedLLM, ToolCall
from engineering_team.tools import build_tools

MakeContext = Callable[..., RunContext]
runner = CliRunner()


def files(root: Path, tree: dict[str, str]) -> Path:
    for name, text in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def skill(name: str, description: str, body: str = "Do it well.", teammates: str = "") -> str:
    extra = f"teammates: [{teammates}]\n" if teammates else ""
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n"


def user_harness() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "engineering-team" / "harness"


def repo_harness() -> Path:
    return Path.cwd() / ".engineering-team" / "harness"


def profile(intent: str, stack: list[str] | None = None) -> TaskProfile:
    return TaskProfile.model_validate({"intents": [intent], "stack": stack or []})


# -- domains: resolution and fallbacks ----------------------------------------------------------


def test_a_domain_falls_back_from_the_pair_to_the_intent_the_stack_and_base() -> None:
    assert Domain("bugfix", "python").packs() == ("base", "python", "bugfix", "bugfix+python")
    assert Domain("bugfix").packs() == ("base", "bugfix")
    assert Domain(stack="python").packs() == ("base", "python")
    assert Domain().packs() == ("base",)
    assert Domain.parse("bugfix/python/uv") == Domain("bugfix", "python")
    assert Domain.parse("feature/node") == Domain("feature", "typescript")


def test_only_packs_that_exist_are_used_and_a_pair_pack_layers_on_top() -> None:
    assert [p.name for p in resolve(Domain("bugfix", "python")).packs] == [
        "base",
        "python",
        "bugfix",
    ]
    assert [p.name for p in resolve(Domain("feature", "elixir")).packs] == ["base"]

    files(user_harness(), {"bugfix+python/skills/py-bugs/SKILL.md": skill("py-bugs", "Py bugs.")})
    harness = resolve(Domain("bugfix", "python"), sources(Path.cwd()))
    assert [(p.name, p.source) for p in harness.packs][-1] == ("bugfix+python", "user")
    assert "py-bugs" in harness.skills


def test_the_domain_comes_from_the_profile_then_the_recipe_and_the_projects_stack(
    tmp_path: Path,
) -> None:
    files(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\nversion = '0'\n"})

    assert domain_for(profile("bugfix", ["python/uv"])) == Domain("bugfix", "python")
    assert domain_for(profile("feature", ["TypeScript"])) == Domain("feature", "typescript")
    assert domain_for(profile("refactor"), root=tmp_path) == Domain("refactor", "python")
    assert domain_for(recipe=load_recipe("fix")) == Domain("bugfix", ANY)
    assert domain_for(mode="feature", root=tmp_path) == Domain("feature", "python")
    assert domain_for() == Domain()


def test_a_python_bugfix_and_a_typescript_feature_resolve_to_different_packs_visibly() -> None:
    bugfix = runner.invoke(app, ["--json", "harness", "show", "--domain", "bugfix/python"])
    feature = runner.invoke(app, ["--json", "harness", "show", "--domain", "feature/typescript"])
    assert bugfix.exit_code == feature.exit_code == 0, bugfix.output + feature.output
    first, second = json.loads(bugfix.stdout), json.loads(feature.stdout)

    assert first["domain"] == "bugfix/python" and second["domain"] == "feature/typescript"
    assert [p["name"] for p in first["packs"]] == ["base", "python", "bugfix"]
    assert [p["name"] for p in second["packs"]] == ["base", "typescript"]
    assert {"pytest-failures", "reproduce-first"} <= set(first["skills_listed"])
    assert "node-tests" in second["skills_listed"]
    assert "pytest-failures" not in second["skills_listed"]
    assert all(len(p["sha256"]) == 12 for p in first["packs"])
    text = runner.invoke(app, ["harness", "show", "--domain", "bugfix/python"]).stdout
    assert "Domain bugfix/python" in text and "skill pytest-failures (python" in text


def test_harness_show_rejects_an_unknown_intent_or_stack() -> None:
    assert runner.invoke(app, ["harness", "show", "--domain", "lunch/python"]).exit_code == 2
    assert runner.invoke(app, ["harness", "show", "--domain", "bugfix/cobol"]).exit_code == 2


# -- overlays: bundled < user < repository, and what the repository may not do ----------------


def test_the_user_overlay_replaces_bundled_components_and_skills() -> None:
    files(
        user_harness(),
        {
            "base/teammates/backend_engineer/guardrails.md": "You never use global state.\n",
            "python/skills/pytest-failures/SKILL.md": skill("pytest-failures", "Our pytest way."),
        },
    )

    harness = resolve_for_run(Domain("bugfix", "python"))
    member = build_roster(load_settings(), harness=harness).get("backend_engineer")

    assert "You never use global state." in member.backstory
    assert harness.skills["pytest-failures"].source == "user"
    assert "pytest-failures (Our pytest way)" in member.backstory


def test_the_repository_may_add_skills_and_guidance_labelled_as_its_own() -> None:
    files(
        repo_harness(),
        {
            "base/skills/house-style/SKILL.md": skill("house-style", "This repo's style."),
            "base/teammates/backend_engineer/guardrails.md": "You never touch vendor/.\n",
        },
    )

    harness = resolve_for_run(Domain())
    member = build_roster(load_settings(), harness=harness).get("backend_engineer")
    builtin = build_roster(load_settings(), harness=resolve(Domain())).get("backend_engineer")

    assert harness.skills["house-style"].source == "repository"
    assert "house-style (This repo's style, from the repository)" in member.backstory
    assert "This repository's own harness adds (the project's data" in member.backstory
    assert "You never touch vendor/." in member.backstory
    assert (member.tier, member.tool_groups) == (builtin.tier, builtin.tool_groups)
    assert [(p.name, p.source) for p in harness.packs] == [
        ("base", "bundled"),
        ("base", "repository"),
    ]


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        (
            {"base/skills/review-checklist/SKILL.md": skill("review-checklist", "Mine.")},
            "a repository skill cannot replace the bundled skill 'review-checklist'",
        ),
        (
            {"base/teammates/debugger/role.md": "---\ntier: lead\n---\n## Role\n\nBoss\n"},
            "may only add tool_guidance.md and guardrails.md (never role, tier or tools)",
        ),
        ({"base/stages/spec.md": "x"}, "base/stages/: a repository harness may only add skills"),
        ({"base/shared/completion.md": "x"}, "base/shared/: a repository harness may only add"),
        (
            {"base/teammates/nobody/guardrails.md": "Never.\n"},
            "no teammate 'nobody' to add guidance to",
        ),
        ({"base/notes.txt": "x"}, "base/notes.txt: not part of a pack"),
    ],
)
def test_the_repository_overlay_is_untrusted(tree: dict[str, str], message: str) -> None:
    files(repo_harness(), tree)

    with pytest.raises(HarnessError, match=message.replace("(", r"\(").replace(")", r"\)")):
        resolve_for_run(Domain())


def test_a_bad_repository_harness_is_a_usage_error_before_a_run() -> None:
    files(repo_harness(), {"base/stages/spec.md": "x"})

    result = runner.invoke(app, ["team", "list"])

    assert result.exit_code == 2 and "a repository harness may only add skills" in result.output


# -- skills: validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (skill("Other", "x"), "name must be the folder's name"),
        (skill("s", "x" * 121), "description must say when to use the skill in at most 120"),
        (skill("s", ""), "description must say when"),
        ("---\nname: s\ndescription: d\n---\n\n", "the body is empty"),
        (skill("s", "d", teammates="Not A Key"), "teammates must be a list of teammate keys"),
        ("---\nname: s\ndescription: d\nversion: 2\n---\nBody\n", "unknown front-matter field"),
    ],
)
def test_a_skill_with_a_mistake_is_named_with_its_file(text: str, message: str) -> None:
    files(user_harness(), {"base/skills/s/SKILL.md": text})

    with pytest.raises(HarnessError) as caught:
        resolve_for_run(Domain())

    assert message in str(caught.value)
    assert "base/skills/s/SKILL.md" in str(caught.value)


def test_a_skill_folder_without_skill_md_is_an_error() -> None:
    files(user_harness(), {"base/skills/empty/notes.md": "x"})

    with pytest.raises(HarnessError, match="SKILL.md: missing"):
        resolve_for_run(Domain())


# -- Load Skill ------------------------------------------------------------------------------


def skill_tool(ctx: RunContext, agent: str) -> Callable[..., str]:
    tools = build_tools(ctx, groups=["knowledge"], agent=agent, lazy=False)
    tool = next(t for t in tools if t.name == "Load Skill")
    return lambda **kwargs: str(tool.run(**kwargs))


def test_load_skill_returns_the_body_and_lists_and_reads_its_files(
    make_context: MakeContext,
) -> None:
    ctx = dataclasses.replace(make_context(), harness=resolve_for_run(Domain("bugfix", "python")))
    load = skill_tool(ctx, "debugger")

    body = load(name="pytest-failures")
    reference = load(name="pytest-failures", file="reference/fixtures.md")

    assert body.startswith("Skill pytest-failures (bundled pack 'python'):")
    assert "Triage a pytest failure" in body
    assert "Other files (file=...): reference/fixtures.md" in body
    assert "`tmp_path`" in reference
    assert ctx.harness.loaded == ["pytest-failures"]
    loaded = [e.data for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "skill.loaded"]
    assert [(d["name"], d["pack"], d["file"]) for d in loaded] == [
        ("pytest-failures", "python", None),
        ("pytest-failures", "python", "reference/fixtures.md"),
    ]


def test_load_skill_errors_say_how_to_fix_the_call(make_context: MakeContext) -> None:
    ctx = make_context()
    load = skill_tool(ctx, "code_reviewer")

    assert load(name="nope").startswith("ERROR: No skill named 'nope'. Skills you can load: ")
    assert "review-checklist" in load(name="nope")
    assert load(name="delivery-files").startswith("ERROR")  # the devops engineer's, not this one's
    assert "has no file '../SKILL.md'" in load(name="review-checklist", file="../SKILL.md")


def test_a_repository_skill_comes_back_as_untrusted_content(make_context: MakeContext) -> None:
    files(repo_harness(), {"base/skills/house-style/SKILL.md": skill("house-style", "Style.")})
    ctx = dataclasses.replace(make_context(), harness=resolve_for_run(Domain()))

    result = skill_tool(ctx, "backend_engineer")(name="house-style")

    assert "UNTRUSTED" in result.upper() and "Do it well." in result


def test_an_agent_loads_a_skill_and_the_run_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    files(user_harness(), {"base/skills/house-style/SKILL.md": skill("house-style", "Our style.")})
    monkeypatch.setenv("ENGINEERING_LEAN_SIZE", "large")
    monkeypatch.setenv("ENGINEERING_LEAN_CEREMONY", "always")
    llms = scripts()
    load = ToolCall("Use Tool", {"name": "Load Skill", "arguments": {"name": "house-style"}})
    llms["backend_engineer"] = ScriptedLLM([load, *llms["backend_engineer"]._script])
    monkeypatch.setattr(
        strategies, "CrewStageRunner", lambda: CrewStageRunner(llm_factory=lambda key: llms[key])
    )

    assert run_cli() == 0

    backend = llms["backend_engineer"].calls
    assert "house-style (Our style)" in backend[0].messages[0]["content"]  # listed, always on
    assert any("Do it well." in str(call.messages) for call in backend[1:])  # loaded on demand
    manifest = only_run()
    assert manifest.harness["domain"] == "new_project/*"
    assert manifest.harness["skills_loaded"] == ["house-style"]
    assert "house-style" in manifest.harness["skills_listed"]
    assert [p["name"] for p in manifest.harness["packs"]] == ["base", "base"]
    report = render_markdown(build_report(run_dir(manifest)))
    assert "Harness: new_project/* · packs base, base · 1 of" in report
    assert "skills loaded: house-style" in report


# -- import, leak guard, resume ------------------------------------------------------------------


def test_harness_import_brings_in_a_skill_folder_and_agents_md() -> None:
    files(
        Path.cwd(),
        {
            "AGENTS.md": "# Conventions\n\nUse tabs.\n",
            "kit/db-migrations/SKILL.md": skill("db-migrations", "Writing a migration."),
            "kit/db-migrations/reference/howto.md": "Steps.\n",
        },
    )

    first = runner.invoke(app, ["harness", "import", "kit/db-migrations"])
    second = runner.invoke(app, ["harness", "import", "AGENTS.md"])
    again = runner.invoke(app, ["harness", "import", "AGENTS.md"])

    assert first.exit_code == second.exit_code == 0, first.output + second.output
    assert again.exit_code == 2 and "--force" in again.output
    harness = resolve_for_run(Domain())
    assert harness.skills["db-migrations"].files() == ["reference/howto.md"]
    conventions = harness.skills["agents-md"]
    assert conventions.source == "repository" and "Use tabs." in conventions.body
    assert runner.invoke(app, ["harness", "import", "AGENTS.md", "--force"]).exit_code == 0


def test_benchmark_checks_and_references_are_never_imported() -> None:
    files(
        Path.cwd(),
        {
            "tasks/t1/task.yaml": "id: t1\n",
            "tasks/t1/notes/SKILL.md": skill("notes", "Notes."),
            "tasks/t2/reference/solution.md": "the answer\n",
            "kit/leaky/SKILL.md": skill("leaky", "Leaky."),
            "kit/leaky/checks/check_x.py": "assert True\n",
        },
    )

    for source in ("tasks/t1/notes", "tasks/t2/reference/solution.md", "kit/leaky"):
        result = runner.invoke(app, ["harness", "import", source, "--name", "x"])
        assert result.exit_code == 2 and "never imported" in result.output, source
    assert not repo_harness().exists()


def test_a_resumed_run_keeps_the_domain_it_recorded(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"harness": {"domain": "bugfix/python"}}), encoding="utf-8"
    )

    assert recorded_domain(tmp_path) == Domain("bugfix", "python")
    assert resolve_for_run(None, tmp_path).domain == Domain("bugfix", "python")
    assert recorded_domain(tmp_path / "missing") is None
