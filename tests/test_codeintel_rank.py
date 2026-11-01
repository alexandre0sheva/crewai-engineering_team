"""Ranking files for a request (T24): a personalised PageRank over the import graph, seeded by the
request, a stack trace, triage focus paths and the reproduction, lifted by Git recency."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.codeintel.git import Commit, FileChange
from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.lexical import code_words, request_terms
from engineering_team.codeintel.rank import Ranked, rank_files, recency_weights
from engineering_team.codeintel.repomap import build_ranking, render_map
from engineering_team.codeintel.seeds import Seeds, resolve_path, seeds_from
from engineering_team.tools.workspace import ProjectWorkspace

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "benchmarks" / "tasks" / "ledger-misleading-trace"

FILES = {
    "shop/__init__.py": "",
    "shop/__main__.py": (
        "from shop.checkout import checkout\n\n\ndef main():\n    return checkout([1, 2])\n"
    ),
    "shop/checkout.py": (
        "from shop.discounts import apply_discount\nfrom shop.pricing import compute_total\n"
        "from shop.tax import add_tax\n\n\ndef checkout(cart):\n"
        "    return add_tax(apply_discount(compute_total(cart)))\n"
    ),
    "shop/pricing.py": (
        "from shop.money import cents\n\n\ndef compute_total(cart):\n"
        "    return sum(cents(item) for item in cart)\n"
    ),
    "shop/discounts.py": (
        "from shop.money import cents\n\n\ndef apply_discount(amount):\n"
        "    # the voucher rate is read from the loyalty table\n"
        "    return cents(amount * 0.9)\n"
    ),
    "shop/tax.py": (
        "from shop.money import cents\n\n\ndef add_tax(amount):\n    return cents(amount)\n"
    ),
    "shop/money.py": "def cents(value):\n    return round(value * 100)\n",
    "shop/notes.py": '"""Notes about nothing in particular."""\n\nLINES = ["alpha", "beta"]\n',
    "tests/test_pricing.py": (
        "from shop.pricing import compute_total\n\n\ndef test_total():\n"
        "    assert compute_total([1]) == 100\n"
    ),
    "tests/test_tax.py": (
        "from shop.tax import add_tax\n\n\ndef test_tax():\n    assert add_tax(1)\n"
    ),
}
TRACE = """Traceback (most recent call last):
  File "/home/ci/build/shop/__main__.py", line 5, in main
    return checkout([1, 2])
  File "/home/ci/build/shop/checkout.py", line 7, in checkout
    return add_tax(apply_discount(compute_total(cart)))
  File "/home/ci/build/shop/pricing.py", line 5, in compute_total
    return sum(cents(item) for item in cart)
TypeError: unsupported operand type(s) for *: 'NoneType' and 'float'
"""


@pytest.fixture
def shop(tmp_path: Path) -> ProjectWorkspace:
    workspace = ProjectWorkspace.create(tmp_path / "shop-project")
    for path, text in FILES.items():
        workspace.write_file(path, text)
    return workspace


def ranking(
    workspace: ProjectWorkspace, seeds: Seeds | None = None, **kwargs: object
) -> list[Ranked]:
    index = SourceIndex.build(workspace)
    return rank_files(index, ImportGraph(index), seeds or Seeds(), **kwargs)  # type: ignore[arg-type]


def order(found: list[Ranked]) -> list[str]:
    return [item.path for item in found]


def seeded(workspace: ProjectWorkspace, text: str = "", **kwargs: object) -> list[Ranked]:
    index = SourceIndex.build(workspace)
    seeds = seeds_from(index, text, **kwargs)  # type: ignore[arg-type]
    return rank_files(index, ImportGraph(index), seeds)


# -- seeds ---------------------------------------------------------------------------------------


def test_a_path_from_another_checkout_resolves_to_the_project_file(shop: ProjectWorkspace) -> None:
    index = SourceIndex.build(shop)

    assert resolve_path(index, "/home/ci/build/shop/pricing.py") == "shop/pricing.py"
    assert resolve_path(index, "C:\\work\\shop\\tax.py") == "shop/tax.py"
    assert resolve_path(index, "pricing.py") == "shop/pricing.py"  # a unique file name
    assert resolve_path(index, "/usr/lib/python3/json/decoder.py") is None
    assert resolve_path(index, "<frozen runpy>") is None


def test_a_trace_seeds_its_project_files_innermost_first(shop: ProjectWorkspace) -> None:
    index = SourceIndex.build(shop)

    seeds = seeds_from(index, TRACE)

    assert list(seeds.paths) == ["shop/pricing.py", "shop/checkout.py", "shop/__main__.py"]
    weights = list(seeds.paths.values())
    assert weights == sorted(weights, reverse=True) and weights[0] == 1.0


def test_only_identifiers_written_like_code_seed_by_symbol(shop: ProjectWorkspace) -> None:
    index = SourceIndex.build(shop)

    prose = seeds_from(index, "The total is wrong after I add a tax; cents look off.")
    code = seeds_from(index, "compute_total returns None; also see `cents` and apply_discount()")

    assert prose.symbols == frozenset()
    assert code.symbols == {"compute_total", "cents", "apply_discount"}


def test_focus_paths_and_reproduction_files_seed_files_and_folders(shop: ProjectWorkspace) -> None:
    index = SourceIndex.build(shop)

    seeds = seeds_from(index, "", focus_paths=["shop/tax.py", "tests"], files=["tests/test_tax.py"])

    assert seeds.paths["shop/tax.py"] == 1.0
    assert seeds.paths["tests/test_tax.py"] == 1.0  # the reproduction's file beats a folder's 0.5
    assert seeds.paths["tests/test_pricing.py"] == 0.5


def test_the_terms_come_from_prose_not_from_code_blocks_or_traces() -> None:
    text = (
        "The voucher discount is missing after checkout.\n\n```text\n$ shop --frobnicate\n```\n"
        + TRACE
    )

    terms = request_terms(text, files=10)

    assert "voucher" in terms and "discount" in terms
    assert "frobnicate" not in terms and "traceback" not in terms and "checkout" in terms
    assert len({term[:5] for term in terms}) == len(terms)  # one term per word stem
    assert request_terms(text, files=0) == ()


def test_comments_and_docstrings_are_not_code_words(shop: ProjectWorkspace) -> None:
    index = SourceIndex.build(shop)

    assert "voucher" not in code_words(index.files["shop/discounts.py"])
    assert "Notes about" not in code_words(index.files["shop/notes.py"])
    assert "apply_discount" in code_words(index.files["shop/discounts.py"])


# -- ranking -------------------------------------------------------------------------------------


def test_the_module_a_stack_trace_ends_in_ranks_first(shop: ProjectWorkspace) -> None:
    found = seeded(shop, TRACE)

    assert found[0].path == "shop/pricing.py"
    assert found[0].score == 1.0
    assert "named in the request" in found[0].why and "defines `compute_total`" in found[0].why


def test_files_next_to_the_trace_outrank_unrelated_ones(shop: ProjectWorkspace) -> None:
    ranks = {item.path: position for position, item in enumerate(seeded(shop, TRACE))}

    # The test that imports the crashing module, and the helper it calls, against a loner.
    assert ranks["tests/test_pricing.py"] < ranks["shop/notes.py"]
    assert ranks["shop/money.py"] < ranks["shop/notes.py"]
    assert ranks["shop/notes.py"] > ranks["shop/tax.py"]


def test_the_words_of_the_request_find_a_module_the_trace_does_not_name(
    shop: ProjectWorkspace,
) -> None:
    prose = "Checkout crashes when a customer applies a discount."

    without_trace = order(seeded(shop, prose))
    with_trace = seeded(shop, prose + "\n" + TRACE)

    assert without_trace[:2] == ["shop/checkout.py", "shop/discounts.py"]
    ranks = {item.path: position for position, item in enumerate(with_trace)}
    assert ranks["shop/discounts.py"] < ranks["shop/tax.py"] < ranks["tests/test_tax.py"]
    assert "mentions 'discount'" in with_trace[ranks["shop/discounts.py"]].why


def test_a_word_only_a_comment_uses_does_not_pull_the_file_in(shop: ProjectWorkspace) -> None:
    found = seeded(shop, "The voucher loyalty rate is wrong.")

    assert "shop/discounts.py" not in order(found)[:3] or not any(
        "voucher" in why for item in found for why in item.why
    )


def test_without_a_seed_the_files_everything_imports_come_first(shop: ProjectWorkspace) -> None:
    found = ranking(shop)

    assert found[0].path == "shop/money.py"
    assert found[-1].path in {"shop/notes.py", "shop/__init__.py"}


def test_ranking_is_deterministic(shop: ProjectWorkspace) -> None:
    first = order(seeded(shop, TRACE))
    second = order(seeded(shop, TRACE))

    assert first == second
    assert sorted(first) == sorted(FILES)  # every file, once


def test_a_workspace_without_source_ranks_nothing(tmp_path: Path) -> None:
    empty = ProjectWorkspace.create(tmp_path / "empty")
    empty.write_file("README.md", "# nothing\n")

    assert ranking(empty) == []


def test_git_recency_lifts_recently_changed_files(shop: ProjectWorkspace) -> None:
    now = 1_000_000_000.0
    day = 86_400
    commits = [
        Commit("a", int(now - 1 * day), (FileChange("shop/tax.py", 1, 0),)),
        Commit("a", int(now - 80 * day), (FileChange("shop/discounts.py", 1, 0),)),
        Commit("a", int(now - 400 * day), (FileChange("shop/notes.py", 1, 0),)),
    ]

    weights = recency_weights(commits, now, days=90)

    assert weights["shop/tax.py"] > 0.8 > weights["shop/discounts.py"] > 0
    assert "shop/notes.py" not in weights and recency_weights(commits, now, days=0) == {}
    plain = {item.path: item.score for item in ranking(shop)}
    lifted = {item.path: item.score for item in ranking(shop, recency=weights)}
    assert (
        lifted["shop/tax.py"] / lifted["shop/money.py"]
        > plain["shop/tax.py"] / plain["shop/money.py"]
    )
    assert any("changed recently" in item.why for item in ranking(shop, recency=weights))


# -- the map -------------------------------------------------------------------------------------


def test_the_map_shows_as_many_files_as_the_budget_allows_best_first(
    shop: ProjectWorkspace,
) -> None:
    found = build_ranking(shop, TRACE)

    small = render_map(found, 20)
    large = render_map(found, 4000)

    assert small.files == ("shop/pricing.py",)  # one file is always shown
    assert small.files == large.files[:1] and len(large.files) > len(small.files)
    assert small.candidates == large.candidates
    assert large.text.startswith("shop/pricing.py") and "function compute_total  L4" in large.text
    assert large.tokens <= 4000 and small.tokens <= 120


def test_the_map_header_and_scope(shop: ProjectWorkspace) -> None:
    found = build_ranking(shop, TRACE)

    scoped = render_map(found, 4000, prefix="tests", header="Map:")

    assert scoped.text.splitlines()[0] == "Map:"
    assert scoped.files and all(path.startswith("tests/") for path in scoped.files)


# -- the larger benchmark fixture ----------------------------------------------------------------


def test_the_misleading_trace_task_puts_the_module_with_the_bug_in_the_map() -> None:
    """The traceback ends in fx/convert.py; the bug is in io/bank_formats.py (T24 done-when)."""

    request = (LEDGER / "request.md").read_text(encoding="utf-8")
    trace = (LEDGER / "trace.txt").read_text(encoding="utf-8")
    workspace = ProjectWorkspace(LEDGER / "fixture")

    found = build_ranking(workspace, request + "\n" + trace)

    assert found.ranked[0].path == "ledgerly/fx/convert.py"
    small = render_map(found, 500)  # a fifth of the default pack
    assert "ledgerly/io/bank_formats.py" in small.files
    assert "function parse_amount" in small.text
