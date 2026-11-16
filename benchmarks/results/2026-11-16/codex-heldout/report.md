# Benchmark report: codex-heldout

- **Provider / profile:** default / default
- **Strategies and agents:** codex
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:06:09+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent codex:** codex-cli 0.162.1; model: its own default; billing: subscription

> **Small sample:** 16 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex | 10/16 | 62% (39%–82%) | plan | unknown | 98s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| codex | – | – | 139,821 | 90% | plan | 98s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex · brownfield · heldout | 8/12 | 67% (39%–86%) | plan | unknown | 98s | 0.0 | 0 | 0 |
| codex · greenfield · heldout | 2/4 | 50% (15%–85%) | plan | unknown | 109s | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous-export · codex | 0/2 | 0% (0%–66%) | plan | – | 71s | 0.0 | 0 | 0 |
| answer-questions · codex | 2/2 | 100% (34%–100%) | plan | unknown | 46s | 0.0 | 0 | 0 |
| behaviour-refactor · codex | 2/2 | 100% (34%–100%) | plan | unknown | 102s | 0.0 | 0 | 0 |
| cron-scheduler · codex | 2/2 | 100% (34%–100%) | plan | unknown | 2.6min | 0.0 | 0 | 0 |
| dependency-upgrade · codex | 0/2 | 0% (0%–66%) | plan | – | 2.1min | 0.0 | 0 | 0 |
| go-cli-feature · codex | 2/2 | 100% (34%–100%) | plan | unknown | 108s | 0.0 | 0 | 0 |
| ledger-misleading-trace · codex | 0/2 | 0% (0%–66%) | plan | – | 94s | 0.0 | 0 | 0 |
| react-component-bug · codex | 2/2 | 100% (34%–100%) | plan | unknown | 77s | 0.0 | 0 | 0 |

## Failures (6)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ambiguous-export | codex | 1 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| ambiguous-export | codex | 2 | failed | assumption-recorded, behaviour-matches-assumption | criteria not met: assumption-recorded, behaviour-matches-assumption |
| dependency-upgrade | codex | 1 | failed | tests-pass | criteria not met: tests-pass |
| dependency-upgrade | codex | 2 | failed | tests-pass | criteria not met: tests-pass |
| ledger-misleading-trace | codex | 1 | failed | others-unchanged | criteria not met: others-unchanged |
| ledger-misleading-trace | codex | 2 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
