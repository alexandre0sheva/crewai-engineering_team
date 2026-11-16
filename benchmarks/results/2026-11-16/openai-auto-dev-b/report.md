# Benchmark report: openai-auto-dev-b

- **Provider / profile:** openai / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T21:42:56+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 5/6 | 83% (44%–97%) | $0.0257 | $0.0308 | 2.6min | 0.7 | 24 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 5.3 | 34.2 | 189,758 | 82% | $0.0257 | 2.6min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · dev | 5/6 | 83% (44%–97%) | $0.0257 | $0.0308 | 2.6min | 0.7 | 24 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · auto | 2/2 | 100% (34%–100%) | $0.0200 | $0.0200 | 2.6min | 1.0 | 6 | 0 |
| docs-cli-reference · auto | 1/2 | 50% (9%–91%) | $0.0263 | $0.0526 | 2.9min | 1.0 | 7 | 0 |
| multi-file-refactor · auto | 2/2 | 100% (34%–100%) | $0.0307 | $0.0307 | 2.4min | 0.0 | 11 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| docs-cli-reference | auto | 1 | failed | defaults-stated | criteria not met: defaults-stated |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
