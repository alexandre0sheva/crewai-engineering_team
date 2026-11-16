# Benchmark report: claude-code-heldout

- **Provider / profile:** default / default
- **Strategies and agents:** claude-code
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:06:09+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent claude-code:** 2.1.287 (Claude Code); model: claude-sonnet-5-5; billing: subscription

> **Small sample:** 16 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code | 10/16 | 62% (39%–82%) | plan (≈$0.0981) | unknown | 33s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| claude-code | – | – | 116,662 | 85% | plan (≈$0.0981) | 33s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code · brownfield · heldout | 8/12 | 67% (39%–86%) | plan (≈$0.0945) | unknown | 31s | 0.0 | 0 | 0 |
| claude-code · greenfield · heldout | 2/4 | 50% (15%–85%) | plan (≈$0.11) | unknown | 42s | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · claude-code | 0/2 | 0% (0%–66%) | plan (≈$0.11) | – | 40s | 0.0 | 0 | 0 |
| answer-questions · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0828) | unknown | 27s | 0.0 | 0 | 0 |
| behaviour-refactor · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0747) | unknown | 29s | 0.0 | 0 | 0 |
| cron-scheduler · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.11) | unknown | 42s | 0.0 | 0 | 0 |
| dependency-upgrade · claude-code | 0/2 | 0% (0%–66%) | plan (≈$0.10) | – | 34s | 0.0 | 0 | 0 |
| go-cli-feature · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0995) | unknown | 37s | 0.0 | 0 | 0 |
| ledger-misleading-trace · claude-code | 0/2 | 0% (0%–66%) | plan (≈$0.13) | – | 34s | 0.0 | 0 | 0 |
| react-component-bug · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0741) | unknown | 24s | 0.0 | 0 | 0 |

## Failures (6)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | claude-code | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | claude-code | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| dependency-upgrade | claude-code | 1 | failed | tests-pass | criteria not met: tests-pass |
| dependency-upgrade | claude-code | 2 | failed | tests-pass | criteria not met: tests-pass |
| ledger-misleading-trace | claude-code | 1 | failed | others-unchanged | criteria not met: others-unchanged |
| ledger-misleading-trace | claude-code | 2 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
