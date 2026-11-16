# Benchmark report: openai-pipeline-dev-a2

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline
- **Repeats per task:** 1
- **Created:** 2026-11-16T21:50:54+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 6 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 4/6 | 67% (30%–90%) | $0.0249 | $0.0374 | 105s | 1.0 | 24 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 4.8 | 33.7 | 268,528 | 84% | $0.0249 | 105s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · dev | 4/6 | 67% (30%–90%) | $0.0249 | $0.0374 | 105s | 1.0 | 24 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| express-api-feature · pipeline | 0/1 | 0% (0%–79%) | $0.0460 | – | 7.0min | 3.0 | 15 | 0 |
| legacy-feature · pipeline | 0/1 | 0% (0%–79%) | $0.0179 | – | 4.6min | 3.0 | 4 | 0 |
| multi-file-refactor · pipeline | 1/1 | 100% (21%–100%) | $0.0248 | $0.0248 | 2.3min | 0.0 | 3 | 0 |
| node-cli-bugfix · pipeline | 1/1 | 100% (21%–100%) | $0.0035 | $0.0035 | 73s | 0.0 | 0 | 0 |
| review-seeded-defects · pipeline | 1/1 | 100% (21%–100%) | $0.0539 | $0.0539 | 67s | 0.0 | 0 | 0 |
| seeded-bug · pipeline | 1/1 | 100% (21%–100%) | $0.0037 | $0.0037 | 73s | 0.0 | 2 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| express-api-feature | pipeline | 1 | failed | tests-added | criteria not met: tests-added (the team ended: Stage 'verify' failed: Not verified (failed): Required check 'acceptance-1' failed: exit code 1. See reports/verification.md (in the run directory).) |
| legacy-feature | pipeline | 1 | failed | existing-tests-pass, tests-added | criteria not met: existing-tests-pass, tests-added (the team ended: Stage 'verify' failed: Not verified (failed): Required check 'tests' failed: 16 passed, 3 failed, 0 skipped; Required check 'acceptance-1' failed: exit code 1. See reports/verification.md (in the run ) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
