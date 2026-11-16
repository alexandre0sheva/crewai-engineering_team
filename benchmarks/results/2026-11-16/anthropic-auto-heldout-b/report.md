# Benchmark report: anthropic-auto-heldout-b

- **Provider / profile:** anthropic / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:04:02+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 2/6 | 33% (10%–70%) | $0.0929 | $0.28 | 3.0min | 0.0 | 3 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 5.7 | 18.5 | 197,958 | 52% | $0.0929 | 3.0min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · heldout | 2/6 | 33% (10%–70%) | $0.0929 | $0.28 | 3.0min | 0.0 | 3 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| answer-questions · auto | 0/2 | 0% (0%–66%) | $0.14 | – | 3.6min | 0.0 | 1 | 0 |
| behaviour-refactor · auto | 2/2 | 100% (34%–100%) | $0.0513 | $0.0513 | 96s | 0.0 | 1 | 0 |
| dependency-upgrade · auto | 0/2 | 0% (0%–66%) | $0.0866 | – | 3.3min | 0.0 | 1 | 0 |

## Failures (4)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| answer-questions | auto | 1 | failed | code-unchanged | criteria not met: code-unchanged |
| answer-questions | auto | 2 | failed | code-unchanged | criteria not met: code-unchanged |
| dependency-upgrade | auto | 1 | failed | tests-pass | criteria not met: tests-pass |
| dependency-upgrade | auto | 2 | failed | tests-pass | criteria not met: tests-pass |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
