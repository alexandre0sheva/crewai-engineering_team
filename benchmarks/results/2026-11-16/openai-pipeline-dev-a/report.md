# Benchmark report: openai-pipeline-dev-a

- **Provider / profile:** openai / default
- **Strategies and agents:** pipeline
- **Repeats per task:** 1
- **Created:** 2026-11-16T20:31:17+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 2 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 1/2 | 50% (9%–91%) | $0.0209 | $0.0418 | 2.7min | 1.0 | 12 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| pipeline | 5.5 | 37.5 | 194,227 | 83% | $0.0209 | 2.7min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · brownfield · dev | 1/2 | 50% (9%–91%) | $0.0209 | $0.0418 | 2.7min | 1.0 | 12 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · pipeline | 1/1 | 100% (21%–100%) | $0.0186 | $0.0186 | 2.7min | 1.0 | 5 | 0 |
| docs-cli-reference · pipeline | 0/1 | 0% (0%–79%) | $0.0232 | – | 2.7min | 1.0 | 7 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| docs-cli-reference | pipeline | 1 | failed | defaults-stated | criteria not met: defaults-stated |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
