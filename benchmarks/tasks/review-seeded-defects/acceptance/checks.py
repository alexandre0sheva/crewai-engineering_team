from pathlib import Path

from engineering_team.bench.checklib import expect, main
from engineering_team.bench.reviewlib import SERIOUS, describe, load_defects, load_findings, score

TASK = Path(__file__).resolve().parents[1]
DEFECTS = load_defects(TASK)


def scored(ws):
    return score(load_findings(ws), DEFECTS)


def check_findings_written(ws):
    expect(load_findings(ws) is not None, "no findings")


def check_recall(ws):
    result = scored(ws)
    missed = [d["id"] for d in DEFECTS if d["id"] not in result.matched]
    print(f"recall {result.recall(len(DEFECTS)):.2f}; missed: {', '.join(missed) or 'none'}")
    expect(
        len(result.matched) >= 3,
        f"found {len(result.matched)} of 4 seeded defects; missed {missed}",
    )


def check_serious_rated(ws):
    result = scored(ws)
    serious = [d["id"] for d in DEFECTS if d["serious"] and d["id"] in result.matched]
    expect(serious, "none of the security defects was found")
    for defect in serious:
        finding = result.matched[defect]
        expect(finding.get("severity") in SERIOUS, f"{defect} rated too low: {describe(finding)}")


def check_precision(ws):
    result = scored(ws)
    print(f"precision {result.precision:.2f} over {result.findings} finding(s)")
    expect(result.findings > 0, "no finding of medium severity or above")
    expect(
        result.precision >= 0.5,
        "most findings are not about the seeded defects: "
        + "; ".join(describe(f) for f in result.false_positives[:5]),
    )


if __name__ == "__main__":
    main()
