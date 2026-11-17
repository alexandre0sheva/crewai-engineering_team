# Benchmark report: amend-openai-rungs-ledger

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline, team
- **Repeats per task:** 1
- **Created:** 2026-11-17T12:13:40+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 1/1 | 100% (21%–100%) | $0.0879 | $0.0879 | 14.7min | 1.0 | 9 | 0 |
| team | 1/1 | 100% (21%–100%) | $0.0359 | $0.0359 | 2.2min | 0.0 | 5 | 0 |

## Paired comparison, per task

| Pair | Tasks compared | First wins | Second wins | Ties | Sign test p |
| --- | --- | --- | --- | --- | --- |
| pipeline vs team | 1 | 0 | 0 | 1 | 1.000 |

Passed / counted runs per task:

| Task | pipeline | team |
| --- | --- | --- |
| ledger-misleading-trace | 1/1 | 1/1 |

A contender wins a task when its pass rate there is higher. With one run per task a win is one pass against one failure; read the counts, not the order. *Sign test p* is the exact two-sided probability of a split this uneven if neither were better; "better" or "worse" is claimed only below 0.05 (or when the pass-rate intervals do not overlap), otherwise the pair is not distinguishable at this sample size.

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 13.0 | 68.0 | 775,504 | 78% | $0.0879 | 14.7min |
| team | 3.0 | 23.0 | 255,756 | 81% | $0.0359 | 2.2min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · heldout | 1/1 | 100% (21%–100%) | $0.0879 | $0.0879 | 14.7min | 1.0 | 9 | 0 |
| team · brownfield · heldout | 1/1 | 100% (21%–100%) | $0.0359 | $0.0359 | 2.2min | 0.0 | 5 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ledger-misleading-trace · pipeline | 1/1 | 100% (21%–100%) | $0.0879 | $0.0879 | 14.7min | 1.0 | 9 | 0 |
| ledger-misleading-trace · team | 1/1 | 100% (21%–100%) | $0.0359 | $0.0359 | 2.2min | 0.0 | 5 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
