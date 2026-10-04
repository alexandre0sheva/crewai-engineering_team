# Benchmark report: screen-default

- **Provider / profile:** openai / default
- **Strategies:** single, pipeline
- **Repeats per task:** 2
- **Created:** 2026-10-04T15:46:05+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.23, python 3.12.12, platform Darwin arm64

## Results by strategy

| Strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline | 7/10 | 70% (40%–89%) | $0.11 | $0.16 | 10.6min | 0.7 | 150 | 0 |
| single | 7/10 | 70% (40%–89%) | $0.0048 | $0.0069 | 70s | 0.0 | 7 | 0 |

## By task kind and subset

| Strategy / kind / subset | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pipeline · greenfield · dev | 7/10 | 70% (40%–89%) | $0.11 | $0.16 | 10.6min | 0.7 | 150 | 0 |
| single · greenfield · dev | 7/10 | 70% (40%–89%) | $0.0048 | $0.0069 | 70s | 0.0 | 7 | 0 |

## By task

| Task / strategy | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| csv-validator · pipeline | 2/2 | 100% (34%–100%) | $0.14 | $0.14 | 13.6min | 1.0 | 28 | 0 |
| csv-validator · single | 1/2 | 50% (9%–91%) | $0.0070 | $0.0139 | 101s | 0.0 | 4 | 0 |
| markdown-html · pipeline | 0/2 | 0% (0%–66%) | $0.0742 | – | 10.7min | 0.5 | 26 | 0 |
| markdown-html · single | 0/2 | 0% (0%–66%) | $0.0040 | – | 66s | 0.0 | 0 | 0 |
| notes-cli · pipeline | 2/2 | 100% (34%–100%) | $0.12 | $0.12 | 10.4min | 0.5 | 41 | 0 |
| notes-cli · single | 2/2 | 100% (34%–100%) | $0.0040 | $0.0040 | 62s | 0.0 | 1 | 0 |
| todo-web · pipeline | 2/2 | 100% (34%–100%) | $0.13 | $0.13 | 14.7min | 1.0 | 30 | 0 |
| todo-web · single | 2/2 | 100% (34%–100%) | $0.0050 | $0.0050 | 76s | 0.0 | 1 | 0 |
| url-shortener · pipeline | 1/2 | 50% (9%–91%) | $0.0782 | $0.16 | 6.4min | 0.5 | 25 | 0 |
| url-shortener · single | 2/2 | 100% (34%–100%) | $0.0042 | $0.0042 | 70s | 0.0 | 1 | 0 |

## Failures (6)

| Task | Strategy | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| csv-validator | single | 2 | failed | constraints | criteria not met: constraints |
| markdown-html | pipeline | 1 | failed | headings-and-paragraphs, inline-formatting, lists, code-blocks, quotes-and-rules, escaping, cli | criteria not met: headings-and-paragraphs, inline-formatting, lists, code-blocks, quotes-and-rules, escaping, cli (the team ended: Stage 'plan' failed: The plan cannot be run: WP-3 owns 'README.md', which covers shared file(s) README.md; shared files belong to foundation and integrate. Fix the plan so that every work package owns) |
| markdown-html | pipeline | 2 | failed | inline-formatting, code-blocks | criteria not met: inline-formatting, code-blocks |
| markdown-html | single | 1 | failed | inline-formatting, code-blocks | criteria not met: inline-formatting, code-blocks |
| markdown-html | single | 2 | failed | inline-formatting | criteria not met: inline-formatting |
| url-shortener | pipeline | 2 | failed | shorten-and-redirect, same-url-same-code, validation, stats, persistence | criteria not met: shorten-and-redirect, same-url-same-code, validation, stats, persistence (the team ended: Stage 'plan' failed: The plan cannot be run: WP-1 owns 'README.md', which covers shared file(s) README.md; shared files belong to foundation and integrate. Fix the plan so that every work package owns) |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
