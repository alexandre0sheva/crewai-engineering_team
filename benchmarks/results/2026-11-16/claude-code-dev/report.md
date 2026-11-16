# Benchmark report: claude-code-dev

- **Provider / profile:** default / default
- **Strategies and agents:** claude-code
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:24:58+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent claude-code:** 2.1.287 (Claude Code); model: claude-sonnet-5-5; billing: subscription

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code | 22/24 | 92% (74%–98%) | plan (≈$0.10) | unknown | 40s | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| claude-code | – | – | 107,019 | 85% | plan (≈$0.10) | 40s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code · brownfield · dev | 12/14 | 86% (60%–96%) | plan (≈$0.0873) | unknown | 31s | 0.0 | 0 | 0 |
| claude-code · greenfield · dev | 10/10 | 100% (72%–100%) | plan (≈$0.13) | unknown | 56s | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0985) | unknown | 40s | 0.0 | 0 | 0 |
| csv-validator · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.16) | unknown | 3.3min | 0.0 | 0 | 0 |
| docs-cli-reference · claude-code | 0/2 | 0% (0%–66%) | plan (≈$0.0971) | – | 91s | 0.0 | 0 | 0 |
| express-api-feature · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.12) | unknown | 41s | 0.0 | 0 | 0 |
| legacy-feature · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0883) | unknown | 30s | 0.0 | 0 | 0 |
| markdown-html · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.11) | unknown | 49s | 0.0 | 0 | 0 |
| multi-file-refactor · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0843) | unknown | 28s | 0.0 | 0 | 0 |
| node-cli-bugfix · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0622) | unknown | 20s | 0.0 | 0 | 0 |
| notes-cli · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0729) | unknown | 30s | 0.0 | 0 | 0 |
| seeded-bug · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.0634) | unknown | 23s | 0.0 | 0 | 0 |
| todo-web · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.13) | unknown | 3.3min | 0.0 | 0 | 0 |
| url-shortener · claude-code | 2/2 | 100% (34%–100%) | plan (≈$0.18) | unknown | 5.1min | 0.0 | 0 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| docs-cli-reference | claude-code | 1 | failed | defaults-stated | criteria not met: defaults-stated |
| docs-cli-reference | claude-code | 2 | failed | defaults-stated | criteria not met: defaults-stated |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
