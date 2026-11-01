"""Context packs (T24): the ranked map, facts and lessons of a stage, within its token budget; and
the ``Repo Map`` tool's ``for_request`` ranking."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from conftest import Toolbox
from test_codeintel_rank import FILES, TRACE
from test_repo_memory import fact

from engineering_team.pipeline.context_pack import (
    MAP_LABEL,
    build_pack,
    focus_paths,
    reproduction_files,
)
from engineering_team.pipeline.handoff import handoff_pack
from engineering_team.pipeline.recipes import StageSpec, load_recipe
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.settings import Settings, load_settings
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task

MakeContext = Callable[..., RunContext]
REQUEST = "The order total crashes with a TypeError.\n\n" + TRACE


def shop_context(make_context: MakeContext, settings: Settings | None = None) -> RunContext:
    ctx = make_context("shop", settings=settings)
    for path, text in FILES.items():
        ctx.workspace.write_file(path, text)
    (ctx.run_dir / "request.md").write_text(REQUEST, encoding="utf-8")
    return ctx


def crowd(ctx: RunContext) -> None:
    """More definitions in the files that rank first, so a small budget cannot hold them all."""

    for path in ("shop/pricing.py", "shop/money.py", "shop/checkout.py"):
        extra = "".join(f"\n\ndef helper_{n}():\n    return {n}\n" for n in range(10))
        ctx.workspace.write_file(path, (ctx.workspace.root / path).read_text() + extra)


def implement() -> StageSpec:
    return load_recipe("new").stage("implement")


def events(ctx: RunContext, kind: str) -> list[dict[str, object]]:
    return [e.data for e in read_events(ctx.run_dir / "events.jsonl") if e.type == kind]


# -- settings --------------------------------------------------------------------------------------


def test_the_context_settings_have_defaults_and_per_stage_sizes() -> None:
    settings = load_settings(
        overrides={"context.stage_tokens": 3000, "context.stages": {"review": 800}}
    )

    assert settings.context.tokens_for("implement") == 3000
    assert settings.context.tokens_for("review") == 800
    assert load_settings().context.warn_fraction == 0.6
    assert load_settings().context.recency_days == 90


@pytest.mark.parametrize(
    "override",
    [
        {"context.stage_tokens": 50},
        {"context.stages": {"review": 10}},
        {"context.warn_fraction": 0},
        {"context.warn_fraction": 1.5},
        {"context.recency_days": -1},
        {"context.nonsense": 1},
    ],
)
def test_bad_context_settings_are_refused(override: dict[str, object]) -> None:
    with pytest.raises(Exception, match="context"):
        load_settings(overrides=override)


# -- the pack --------------------------------------------------------------------------------------


def test_the_pack_holds_the_ranked_map_with_the_module_the_trace_ends_in_first(
    make_context: MakeContext,
) -> None:
    ctx = shop_context(make_context)

    pack = build_pack(ctx, PipelineState(), implement())

    assert pack.text.startswith(MAP_LABEL) and "not evidence" in MAP_LABEL
    assert pack.files[0] == "shop/pricing.py" and "shop/money.py" in pack.files
    assert "shop/notes.py" not in pack.files  # nothing links it to the request: noise
    assert pack.sections.keys() == {"map"} and pack.tokens <= pack.budget == 2500
    assert pack.ranked == len(pack.files)


def test_a_small_budget_trims_the_map_and_never_exceeds_it(make_context: MakeContext) -> None:
    ctx = shop_context(make_context, load_settings(overrides={"context.stage_tokens": 200}))
    crowd(ctx)

    pack = build_pack(ctx, PipelineState(), implement())

    assert 0 < len(pack.files) < pack.ranked
    assert pack.files[0] == "shop/pricing.py" and pack.tokens <= 200


def test_a_stage_can_have_its_own_size(make_context: MakeContext) -> None:
    settings = load_settings(overrides={"context.stages": {"implement": 200}})
    ctx = shop_context(make_context, settings)
    crowd(ctx)

    small = build_pack(ctx, PipelineState(), implement())
    larger = build_pack(ctx, PipelineState(), load_recipe("new").stage("design"))

    assert small.budget == 200 and larger.budget == 2500
    assert len(small.files) < len(larger.files)


def test_facts_and_lessons_share_the_budget_and_the_map_gets_the_rest(
    make_context: MakeContext,
) -> None:
    ctx = shop_context(make_context, load_settings(overrides={"context.stage_tokens": 500}))
    for number in range(8):
        ctx.memory.upsert(fact(f"topic-{number}", f"Fact {number} " + "x" * 80, kind="gotcha"))
    section = "\n".join(
        [
            "Lessons (data):",
            *(f"- [L{n}] When something happens: do thing {n} " + "y" * 60 for n in range(8)),
        ]
    )
    (ctx.run_dir / "lessons.json").write_text(
        json.dumps({"ids": [f"L{n}" for n in range(8)], "section": section}), encoding="utf-8"
    )

    pack = build_pack(ctx, PipelineState(), implement())

    assert pack.sections.keys() == {"facts", "lessons", "map"}
    assert pack.sections["facts"] <= 125 and pack.sections["lessons"] <= 125  # a quarter each
    assert pack.tokens <= 500
    assert (
        pack.text.index("Verified facts") < pack.text.index("Lessons") < pack.text.index(MAP_LABEL)
    )


def test_facts_alone_still_make_a_pack_for_a_workspace_without_source(
    make_context: MakeContext,
) -> None:
    ctx = make_context("docs-only")
    ctx.workspace.write_file("README.md", "# hello\n")
    ctx.memory.upsert(fact("a", "The docs are built with mkdocs."))
    (ctx.run_dir / "request.md").write_text("Fix the docs build.", encoding="utf-8")

    pack = build_pack(ctx, PipelineState(), implement())

    assert pack.sections.keys() == {"facts"} and pack.files == ()


def test_the_map_is_left_out_when_context_is_off_or_the_stage_reads_the_code_as_it_is(
    make_context: MakeContext,
) -> None:
    off = shop_context(make_context, load_settings(overrides={"context.enabled": False}))
    on = shop_context(make_context)
    analyze = StageSpec(name="analyze", kind="analyze", teammates=["tech_lead"])

    assert build_pack(off, PipelineState(), implement()).text == ""
    assert build_pack(on, PipelineState(), analyze).text == ""


def test_the_ranking_is_made_once_for_the_same_request_and_files(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.pipeline import context_pack

    ctx = shop_context(make_context)
    built: list[str] = []
    real = context_pack.build_ranking

    def counting(*args: object, **kwargs: object) -> object:
        built.append("ranked")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(context_pack, "build_ranking", counting)

    for _ in range(3):
        build_pack(ctx, PipelineState(), implement())
    assert built == ["ranked"]  # a new file is a new ranking
    ctx.workspace.write_file("shop/extra.py", "from shop.money import cents\n")
    build_pack(ctx, PipelineState(), implement())
    assert built == ["ranked", "ranked"]


def test_a_ranking_that_fails_is_reported_and_the_stage_goes_on(
    make_context: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engineering_team.pipeline import context_pack

    ctx = shop_context(make_context)
    ctx.memory.upsert(fact("a", "A verified fact."))

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("index exploded")

    monkeypatch.setattr(context_pack, "build_ranking", broken)

    pack = build_pack(ctx, PipelineState(), implement())

    assert pack.sections.keys() == {"facts"}
    assert "index exploded" in str(events(ctx, "context.error")[0]["error"])


def test_the_pack_event_names_the_files_and_the_sections(make_context: MakeContext) -> None:
    ctx = shop_context(make_context)

    build_pack(ctx, PipelineState(), implement())

    (event,) = events(ctx, "context.pack")
    assert event["budget_tokens"] == 2500 and event["files"][0] == "shop/pricing.py"  # type: ignore[index]
    assert set(event["sections"]) == {"map"}  # type: ignore[arg-type]


def test_triage_focus_paths_and_the_reproduction_seed_the_ranking(
    make_context: MakeContext,
) -> None:
    from engineering_team.modes.fix_contracts import Repro

    ctx = shop_context(make_context)
    (ctx.run_dir / "request.md").write_text("Something is off.", encoding="utf-8")
    (ctx.run_dir / "task_profile.json").write_text(
        json.dumps({"profile": {"target": {"focus_paths": ["shop/tax.py", 7]}}}), encoding="utf-8"
    )
    state = PipelineState(
        repro=Repro(command="pytest tests/test_pricing.py", files=["tests/test_pricing.py"])
    )

    pack = build_pack(ctx, state, implement())

    assert focus_paths(ctx.run_dir) == ["shop/tax.py"]
    assert reproduction_files(state) == ["tests/test_pricing.py"]
    assert set(pack.files[:2]) == {"shop/tax.py", "tests/test_pricing.py"}
    assert focus_paths(ctx.run_dir / "missing") == []


def test_the_hand_off_carries_the_pack_and_labels_it_as_the_controllers_guide(
    make_context: MakeContext,
) -> None:
    ctx = shop_context(make_context)

    text = handoff_pack(ctx, PipelineState(), implement())

    assert text.startswith("Hand-off from the controller:")
    assert MAP_LABEL in text and text.index(MAP_LABEL) < text.index("Files in the workspace now")


# -- the Repo Map tool -----------------------------------------------------------------------------


def shop_box(make_toolbox: Callable[..., Toolbox]) -> Toolbox:
    box = make_toolbox()
    for path, text in FILES.items():
        box.write(path, text)
    return box


def test_repo_map_ranks_for_a_request_and_says_so(make_toolbox: Callable[..., Toolbox]) -> None:
    box = shop_box(make_toolbox)

    ranked = box("Repo Map", for_request=TRACE, size=500)
    plain = box("Repo Map", size=500)

    lines = ranked.splitlines()
    assert lines[0].startswith("Repo map for ., ranked for the request")
    assert lines[1].startswith("shop/pricing.py") and "named in the request" in lines[1]
    assert "8 of 8 file(s)" in lines[0] and "more file(s)" not in ranked
    assert (
        plain.splitlines()[0].startswith("Repo map for .:")
        and "ranked" not in plain.splitlines()[0]
    )
    assert plain.splitlines()[1].startswith("shop/money.py")  # the most-referenced, as before


def test_repo_map_for_a_request_respects_the_path_and_the_budget(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = shop_box(make_toolbox)

    scoped = box("Repo Map", path="tests", for_request=TRACE)
    tiny = box("Repo Map", for_request=TRACE, size=400)
    large = box("Repo Map", for_request=TRACE, size=4000)

    assert [line for line in scoped.splitlines() if line.startswith(("shop/", "tests/"))] == [
        "tests/test_pricing.py  (next to shop/pricing.py in the import graph)",
        "tests/test_tax.py",
    ]
    assert len(tiny) <= len(large) and "shop/pricing.py" in tiny
    assert box("Repo Map", path="nowhere", for_request=TRACE).startswith("ERROR:")


def test_repo_map_for_a_request_says_what_to_do_when_there_is_no_source(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = make_toolbox()
    box.write("README.md", "# hello\n")

    assert box("Repo Map", for_request="anything").startswith("No supported source files")


def test_an_agent_asks_for_a_map_ranked_for_its_request(make_context: MakeContext) -> None:
    ctx = shop_context(make_context)
    llm = ScriptedLLM(
        [
            ToolCall(
                "Use Tool",
                {"name": "Repo Map", "arguments": {"for_request": TRACE, "size": 600}},
            ),
            "done",
        ]
    )

    run_agent_task(ctx, llm)

    llm.assert_exhausted()
    seen = llm.calls[-1].prompt
    assert "ranked for the request" in seen and "shop/pricing.py" in seen
    assert "ERROR:" not in seen


def test_repo_map_for_a_request_says_how_many_files_it_left_out(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = make_toolbox()
    box.write("pkg/__init__.py", "")
    box.write("pkg/base.py", "VALUE = 1\n")
    for number in range(80):
        box.write(
            f"pkg/m{number:02}.py",
            f"from pkg.base import VALUE\n\n\ndef function_{number}():\n"
            f"    return VALUE + {number}  # a line of padding, to give the file some size\n",
        )

    result = box("Repo Map", for_request="pkg/m00.py crashes", size=400)

    lines = result.splitlines()
    assert lines[1].startswith("pkg/m00.py")
    assert " more file(s) not shown; raise size or pass a subdirectory." in lines[-1]
