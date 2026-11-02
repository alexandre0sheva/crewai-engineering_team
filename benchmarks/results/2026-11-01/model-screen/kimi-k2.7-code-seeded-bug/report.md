# Benchmark report: kimi-k2.7-code-seeded-bug

- **Provider / profile:** default / default
- **Strategies and agents:** auto
- **Repeats per task:** 1
- **Created:** 2026-11-01T22:00:04+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.31, python 3.12.12, platform Darwin arm64

> **Small sample:** 1 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 1/1 | 100% (21%–100%) | $0.0806 | $0.0806 | 2.4min | 0.0 | 7 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 3.0 | 32.0 | 189,596 | 90% | $0.0806 | 2.4min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · dev | 1/1 | 100% (21%–100%) | $0.0806 | $0.0806 | 2.4min | 0.0 | 7 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| seeded-bug · auto | 1/1 | 100% (21%–100%) | $0.0806 | $0.0806 | 2.4min | 0.0 | 7 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
