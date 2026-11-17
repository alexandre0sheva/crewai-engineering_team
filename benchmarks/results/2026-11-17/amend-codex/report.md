# Benchmark report: amend-codex

- **Provider / profile:** default / default
- **Strategies and agents:** codex
- **Repeats per task:** 2
- **Created:** 2026-11-17T11:16:17+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent codex:** codex-cli 0.162.1; model: its own default; billing: subscription

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex | 5/6 | 83% (44%–97%) | plan | unknown | 87s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| codex | – | – | 148,143 | 85% | plan | 87s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex · brownfield · heldout | 5/6 | 83% (44%–97%) | plan | unknown | 87s | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · codex | 1/2 | 50% (9%–91%) | plan | unknown | 36s | 0.0 | 0 | 0 |
| dependency-upgrade · codex | 2/2 | 100% (34%–100%) | plan | unknown | 100s | 0.0 | 0 | 0 |
| ledger-misleading-trace · codex | 2/2 | 100% (34%–100%) | plan | unknown | 106s | 0.0 | 0 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| answer-questions | codex | 2 | failed | q1-free-shipping, q2-heavy-orders, q3-carrier, q4-delivery, citations-real | criteria not met: q1-free-shipping, q2-heavy-orders, q3-carrier, q4-delivery, citations-real |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
