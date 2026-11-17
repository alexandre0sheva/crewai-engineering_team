# Benchmark report: amend-openai-pipeline-dependency

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline
- **Repeats per task:** 2
- **Created:** 2026-11-17T11:40:42+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2/2 | 100% (34%–100%) | $0.0380 | $0.0380 | 4.0min | 1.0 | 29 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 8.0 | 61.0 | 363,379 | 87% | $0.0380 | 4.0min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · heldout | 2/2 | 100% (34%–100%) | $0.0380 | $0.0380 | 4.0min | 1.0 | 29 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dependency-upgrade · pipeline | 2/2 | 100% (34%–100%) | $0.0380 | $0.0380 | 4.0min | 1.0 | 29 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
