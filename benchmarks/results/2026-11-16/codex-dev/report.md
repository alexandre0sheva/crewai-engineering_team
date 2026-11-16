# Benchmark report: codex-dev

- **Provider / profile:** default / default
- **Strategies and agents:** codex
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:24:58+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64
- **Agent codex:** codex-cli 0.162.1; model: its own default; billing: subscription

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex | 22/24 | 92% (74%–98%) | plan | unknown | 2.0min | 0.0 | 0 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| codex | – | – | 155,069 | 91% | plan | 2.0min |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex · brownfield · dev | 12/14 | 86% (60%–96%) | plan | unknown | 84s | 0.0 | 0 | 0 |
| codex · greenfield · dev | 10/10 | 100% (72%–100%) | plan | unknown | 3.5min | 0.0 | 0 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · codex | 2/2 | 100% (34%–100%) | plan | unknown | 2.3min | 0.0 | 0 | 0 |
| csv-validator · codex | 2/2 | 100% (34%–100%) | plan | unknown | 4.3min | 0.0 | 0 | 0 |
| docs-cli-reference · codex | 0/2 | 0% (0%–66%) | plan | – | 84s | 0.0 | 0 | 0 |
| express-api-feature · codex | 2/2 | 100% (34%–100%) | plan | unknown | 2.9min | 0.0 | 0 | 0 |
| legacy-feature · codex | 2/2 | 100% (34%–100%) | plan | unknown | 75s | 0.0 | 0 | 0 |
| markdown-html · codex | 2/2 | 100% (34%–100%) | plan | unknown | 3.5min | 0.0 | 0 | 0 |
| multi-file-refactor · codex | 2/2 | 100% (34%–100%) | plan | unknown | 116s | 0.0 | 0 | 0 |
| node-cli-bugfix · codex | 2/2 | 100% (34%–100%) | plan | unknown | 63s | 0.0 | 0 | 0 |
| notes-cli · codex | 2/2 | 100% (34%–100%) | plan | unknown | 113s | 0.0 | 0 | 0 |
| seeded-bug · codex | 2/2 | 100% (34%–100%) | plan | unknown | 56s | 0.0 | 0 | 0 |
| todo-web · codex | 2/2 | 100% (34%–100%) | plan | unknown | 3.2min | 0.0 | 0 | 0 |
| url-shortener · codex | 2/2 | 100% (34%–100%) | plan | unknown | 3.9min | 0.0 | 0 | 0 |

## Failures (2)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| docs-cli-reference | codex | 1 | failed | defaults-stated | criteria not met: defaults-stated |
| docs-cli-reference | codex | 2 | failed | defaults-stated | criteria not met: defaults-stated |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
