# Benchmark report: opencode-heldout

- **Provider / profile:** default / default
- **Strategies and agents:** opencode
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:26:09+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent opencode:** 1.19.4; model: openrouter/minimax/minimax-m3.1-flash; billing: api

> **Small sample:** 16 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode | 11/16 | 69% (44%–86%) | $0.0131 | $0.0190 | 2.2min | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| opencode | – | – | 612,586 | 92% | $0.0131 | 2.2min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode · brownfield · heldout | 9/12 | 75% (47%–91%) | $0.0109 | $0.0145 | 110s | 0.0 | 0 | 0 |
| opencode · greenfield · heldout | 2/4 | 50% (15%–85%) | $0.0197 | $0.0393 | 4.2min | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · opencode | 0/2 | 0% (0%–66%) | $0.0097 | – | 118s | 0.0 | 0 | 0 |
| answer-questions · opencode | 2/2 | 100% (34%–100%) | $0.0045 | $0.0045 | 109s | 0.0 | 0 | 0 |
| behaviour-refactor · opencode | 2/2 | 100% (34%–100%) | $0.0066 | $0.0066 | 73s | 0.0 | 0 | 0 |
| cron-scheduler · opencode | 2/2 | 100% (34%–100%) | $0.0296 | $0.0296 | 6.3min | 0.0 | 0 | 0 |
| dependency-upgrade · opencode | 1/2 | 50% (9%–91%) | $0.0146 | $0.0293 | 5.7min | 0.0 | 0 | 0 |
| go-cli-feature · opencode | 2/2 | 100% (34%–100%) | $0.0127 | $0.0127 | 2.6min | 0.0 | 0 | 0 |
| ledger-misleading-trace · opencode | 0/2 | 0% (0%–66%) | $0.0192 | – | 4.6min | 0.0 | 0 | 0 |
| react-component-bug · opencode | 2/2 | 100% (34%–100%) | $0.0076 | $0.0076 | 60s | 0.0 | 0 | 0 |

## Failures (5)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | opencode | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | opencode | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| dependency-upgrade | opencode | 2 | failed | tests-pass | criteria not met: tests-pass |
| ledger-misleading-trace | opencode | 1 | failed | others-unchanged | criteria not met: others-unchanged |
| ledger-misleading-trace | opencode | 2 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
