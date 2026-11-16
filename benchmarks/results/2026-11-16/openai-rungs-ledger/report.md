# Benchmark report: openai-rungs-ledger

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline, team
- **Repeats per task:** 1
- **Created:** 2026-11-16T22:02:35+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 0/1 | 0% (0%–79%) | $0.0067 | – | 87s | 0.0 | 4 | 0 |
| team | 0/1 | 0% (0%–79%) | $0.0073 | – | 105s | 0.0 | 4 | 0 |

## Paired comparison, per task

| Pair | Tasks compared | First wins | Second wins | Ties | Sign test p |
| --- | --- | --- | --- | --- | --- |
| pipeline vs team | 1 | 0 | 0 | 1 | 1.000 |

Passed / counted runs per task:

| Task | pipeline | team |
| --- | --- | --- |
| ledger-misleading-trace | 0/1 | 0/1 |

A contender wins a task when its pass rate there is higher. With one run per task a win is one pass against one failure; read the counts, not the order. *Sign test p* is the exact two-sided probability of a split this uneven if neither were better; "better" or "worse" is claimed only below 0.05 (or when the pass-rate intervals do not overlap), otherwise the pair is not distinguishable at this sample size.

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 3.0 | 22.0 | 214,741 | 84% | $0.0067 | 87s |
| team | 3.0 | 23.0 | 221,073 | 83% | $0.0073 | 105s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · heldout | 0/1 | 0% (0%–79%) | $0.0067 | – | 87s | 0.0 | 4 | 0 |
| team · brownfield · heldout | 0/1 | 0% (0%–79%) | $0.0073 | – | 105s | 0.0 | 4 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ledger-misleading-trace · pipeline | 0/1 | 0% (0%–79%) | $0.0067 | – | 87s | 0.0 | 4 | 0 |
| ledger-misleading-trace · team | 0/1 | 0% (0%–79%) | $0.0073 | – | 105s | 0.0 | 4 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| ledger-misleading-trace | pipeline | 1 | failed | unreadable-stops-import, others-unchanged | criteria not met: unreadable-stops-import, others-unchanged |
| ledger-misleading-trace | team | 1 | failed | others-unchanged | criteria not met: others-unchanged |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
