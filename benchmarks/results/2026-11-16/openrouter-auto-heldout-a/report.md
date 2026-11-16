# Benchmark report: openrouter-auto-heldout-a

- **Provider / profile:** openrouter / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T18:56:53+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 10 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 8/10 | 80% (49%–94%) | $0.0400 | $0.0500 | 3.8min | 0.2 | 73 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 2.0 | 34.9 | 507,808 | 88% | $0.0400 | 3.8min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 6/6 | 100% (61%–100%) | $0.0165 | $0.0165 | 2.0min | 0.0 | 39 | 0 |
| auto · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0753 | $0.15 | 7.0min | 0.5 | 34 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · auto | 0/2 | 0% (0%–66%) | $0.0414 | – | 4.9min | 0.5 | 11 | 0 |
| cron-scheduler · auto | 2/2 | 100% (34%–100%) | $0.11 | $0.11 | 12.9min | 0.5 | 23 | 0 |
| go-cli-feature · auto | 2/2 | 100% (34%–100%) | $0.0177 | $0.0177 | 3.0min | 0.0 | 11 | 0 |
| react-component-bug · auto | 2/2 | 100% (34%–100%) | $0.0197 | $0.0197 | 2.9min | 0.0 | 6 | 0 |
| review-auth-change · auto | 2/2 | 100% (34%–100%) | $0.0121 | $0.0121 | 61s | 0.0 | 22 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | auto | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | auto | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
