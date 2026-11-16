# Benchmark report: openai-auto-heldout-a

- **Provider / profile:** openai / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T19:32:51+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 10 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 7/10 | 70% (40%–89%) | $0.0117 | $0.0167 | 75s | 0.3 | 16 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 1.9 | 15.3 | 78,947 | 85% | $0.0117 | 75s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 6/6 | 100% (61%–100%) | $0.0174 | $0.0174 | 77s | 0.0 | 16 | 0 |
| auto · greenfield · heldout | 1/4 | 25% (5%–70%) | $0.0031 | $0.0123 | 71s | 0.8 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · auto | 0/2 | 0% (0%–66%) | $0.0028 | – | 68s | 0.5 | 0 | 0 |
| cron-scheduler · auto | 1/2 | 50% (9%–91%) | $0.0034 | $0.0068 | 77s | 1.0 | 0 | 0 |
| go-cli-feature · auto | 2/2 | 100% (34%–100%) | $0.0033 | $0.0033 | 77s | 0.0 | 6 | 0 |
| react-component-bug · auto | 2/2 | 100% (34%–100%) | $0.0035 | $0.0035 | 85s | 0.0 | 8 | 0 |
| review-auth-change · auto | 2/2 | 100% (34%–100%) | $0.0454 | $0.0454 | 69s | 0.0 | 2 | 0 |

## Failures (3)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | auto | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | auto | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| cron-scheduler | auto | 2 | failed | dom-dow-rule | criteria not met: dom-dow-rule |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
