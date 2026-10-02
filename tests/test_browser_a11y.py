"""Accessibility heuristics over a snapshot (pure text processing, no browser)."""

from __future__ import annotations

from engineering_team.browsertools.a11y import (
    ContrastIssue,
    DomFacts,
    check,
    format_report,
    parse_snapshot,
)

SNAPSHOT = """- generic [ref=e1]:
  - heading "Shop" [level=1] [ref=e2]
  - heading "Deals" [level=3] [ref=e3]
  - button [ref=e4]
  - button "Buy" [ref=e5]
  - link [ref=e6]:
    - /url: /cart
  - link "click here" [ref=e7]:
    - /url: /more
  - textbox [ref=e8]
  - textbox "Email" [ref=e9]
  - img [ref=e10]
  - img "Logo" [ref=e11]
  - checkbox [ref=e12] [checked]
  - combobox "Size" [ref=e13]
  - text: some visible text
"""


def test_the_snapshot_is_parsed_into_roles_names_levels_and_refs() -> None:
    nodes = parse_snapshot(SNAPSHOT)

    by_ref = {node.ref: node for node in nodes}
    assert (
        by_ref["e2"].role == "heading" and by_ref["e2"].name == "Shop" and by_ref["e2"].level == 1
    )
    assert by_ref["e4"].role == "button" and by_ref["e4"].name == ""
    assert by_ref["e5"].name == "Buy"
    assert by_ref["e12"].role == "checkbox"
    assert "e14" not in by_ref and len(nodes) == 13  # "- /url:" and "- text:" lines are not nodes


def _findings(snapshot: str = SNAPSHOT, **facts: object):  # type: ignore[no-untyped-def]
    dom = DomFacts(lang=str(facts.get("lang", "en")), title=str(facts.get("title", "Shop")))
    dom.contrast = list(facts.get("contrast", []))  # type: ignore[call-overload]
    return check(parse_snapshot(snapshot), dom)


def test_unnamed_controls_and_images_are_found_by_ref() -> None:
    found = {(f.rule, f.ref) for f in _findings()}

    assert ("button-name", "e4") in found
    assert ("link-name", "e6") in found
    assert ("field-label", "e8") in found and ("field-label", "e12") in found
    assert ("image-alt", "e10") in found
    assert not {ref for rule, ref in found if rule in ("button-name", "field-label")} & {
        "e5",
        "e9",
        "e13",
    }


def test_headings_skipping_levels_and_missing_or_repeated_h1_are_reported() -> None:
    rules = {(f.rule, f.ref) for f in _findings()}
    no_h1 = _findings('- heading "Only" [level=2] [ref=e1]')
    two_h1 = _findings('- heading "A" [level=1] [ref=e1]\n- heading "B" [level=1] [ref=e2]')

    assert ("heading-order", "e3") in rules  # h1 -> h3
    assert [f.rule for f in no_h1 if f.rule.startswith("h1")] == ["h1-missing"]
    assert [f.rule for f in two_h1 if f.rule.startswith("h1")] == ["h1-multiple"]


def test_vague_link_text_page_language_and_title_are_flagged() -> None:
    rules = {f.rule for f in _findings(lang="", title="")}

    assert {"link-text", "html-lang", "page-title"} <= rules
    assert not {"html-lang", "page-title"} & {f.rule for f in _findings()}


def test_contrast_findings_carry_the_ratio_and_requirement() -> None:
    issue = ContrastIssue(
        text="Pale grey note", ratio=2.1, required=4.5, foreground="#aaaaaa", background="#ffffff"
    )

    finding = next(f for f in _findings(contrast=[issue]) if f.rule == "contrast")

    assert (
        "2.1:1" in finding.message
        and "4.5:1" in finding.message
        and "Pale grey note" in finding.message
    )
    assert finding.severity == "warning"


def test_a_clean_page_has_no_findings() -> None:
    clean = '- heading "Hi" [level=1] [ref=e1]\n- button "Go" [ref=e2]\n- textbox "Name" [ref=e3]'

    assert _findings(clean) == []


def test_the_report_leads_with_counts_orders_by_severity_and_says_it_is_a_heuristic() -> None:
    text = format_report(_findings(), nodes=13)

    lines = text.splitlines()
    assert lines[0].startswith("Accessibility check (heuristic, not a full audit): ")
    assert "error" in lines[0] and "warning" in lines[0]
    severities = [line.split()[0] for line in lines[1:] if line[:1] in "EWI"]
    assert severities == sorted(severities, key=["ERROR", "WARNING", "INFO"].index)
    assert "[e4]" in text and "Run an axe or Lighthouse audit" in lines[-1]
    assert "No problems" in format_report([], nodes=5)
