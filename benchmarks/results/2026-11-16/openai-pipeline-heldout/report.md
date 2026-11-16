# Benchmark report: openai-pipeline-heldout

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:05:00+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 2/6 | 33% (10%–70%) | $0.0226 | $0.0678 | 117s | 0.3 | 40 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 4.7 | 33.7 | 228,994 | 84% | $0.0226 | 117s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · heldout | 0/2 | 0% (0%–66%) | $0.0565 | – | 7.2min | 1.0 | 34 | 0 |
| pipeline · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0056 | $0.0113 | 106s | 0.0 | 6 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · pipeline | 0/2 | 0% (0%–66%) | $0.0048 | – | 104s | 0.0 | 2 | 0 |
| cron-scheduler · pipeline | 2/2 | 100% (34%–100%) | $0.0065 | $0.0065 | 117s | 0.0 | 4 | 0 |
| dependency-upgrade · pipeline | 0/2 | 0% (0%–66%) | $0.0565 | – | 7.2min | 1.0 | 34 | 0 |

## Failures (4)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | pipeline | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | pipeline | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| dependency-upgrade | pipeline | 1 | failed | tests-pass | criteria not met: tests-pass |
| dependency-upgrade | pipeline | 2 | failed | tests-pass | criteria not met: tests-pass |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
