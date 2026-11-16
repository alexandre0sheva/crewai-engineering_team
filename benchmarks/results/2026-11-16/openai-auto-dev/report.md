# Benchmark report: openai-auto-dev

- **Provider / profile:** openai / default
- **Strategies and agents:** auto
- **Repeats per task:** 2
- **Created:** 2026-11-16T20:56:08+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto | 21/21 | 100% (85%–100%) | $0.0159 | $0.0159 | 86s | 0.4 | 39 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| auto | 2.3 | 18.9 | 115,483 | 84% | $0.0159 | 86s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auto · brownfield · dev | 11/11 | 100% (74%–100%) | $0.0267 | $0.0267 | 71s | 0.1 | 26 | 0 |
| auto · greenfield · dev | 10/10 | 100% (72%–100%) | $0.0040 | $0.0040 | 89s | 0.7 | 13 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| add-tests · auto | 1/1 | 100% (21%–100%) | $0.0184 | $0.0184 | 3.9min | 1.0 | 2 | 0 |
| csv-validator · auto | 2/2 | 100% (34%–100%) | $0.0045 | $0.0045 | 92s | 1.0 | 2 | 0 |
| express-api-feature · auto | 2/2 | 100% (34%–100%) | $0.0786 | $0.0786 | 3.2min | 0.0 | 13 | 0 |
| legacy-feature · auto | 2/2 | 100% (34%–100%) | $0.0024 | $0.0024 | 50s | 0.0 | 2 | 0 |
| markdown-html · auto | 2/2 | 100% (34%–100%) | $0.0044 | $0.0044 | 6.6min | 1.0 | 1 | 0 |
| node-cli-bugfix · auto | 2/2 | 100% (34%–100%) | $0.0028 | $0.0028 | 6.0min | 0.0 | 4 | 0 |
| notes-cli · auto | 2/2 | 100% (34%–100%) | $0.0038 | $0.0038 | 89s | 1.0 | 4 | 0 |
| review-seeded-defects · auto | 2/2 | 100% (34%–100%) | $0.0516 | $0.0516 | 70s | 0.0 | 1 | 0 |
| seeded-bug · auto | 2/2 | 100% (34%–100%) | $0.0023 | $0.0023 | 69s | 0.0 | 4 | 0 |
| todo-web · auto | 2/2 | 100% (34%–100%) | $0.0029 | $0.0029 | 74s | 0.0 | 3 | 0 |
| url-shortener · auto | 2/2 | 100% (34%–100%) | $0.0046 | $0.0046 | 104s | 0.5 | 3 | 0 |

## Failures (0)

None: every run passed.

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
