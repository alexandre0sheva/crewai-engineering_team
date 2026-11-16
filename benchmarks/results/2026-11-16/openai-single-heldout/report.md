# Benchmark report: openai-single-heldout

- **Provider / profile:** openai / default
- **Strategies and agents:** single
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:05:00+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 4 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | 2/4 | 50% (15%–85%) | $0.0028 | $0.0056 | 67s | 0.0 | 2 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| single | 1.0 | 12.5 | 56,768 | 93% | $0.0028 | 67s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0028 | $0.0056 | 67s | 0.0 | 2 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · single | 0/2 | 0% (0%–66%) | $0.0025 | – | 63s | 0.0 | 0 | 0 |
| cron-scheduler · single | 2/2 | 100% (34%–100%) | $0.0031 | $0.0031 | 77s | 0.0 | 2 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | single | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | single | 2 | failed | sort-command, assumption-recorded, behaviour-matches-assumption | criteria not met: sort-command, assumption-recorded, behaviour-matches-assumption |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
