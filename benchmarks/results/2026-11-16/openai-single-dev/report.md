# Benchmark report: openai-single-dev

- **Provider / profile:** openai / default
- **Strategies and agents:** single
- **Repeats per task:** 2
- **Created:** 2026-11-16T21:21:58+00:00
- **Versions:** engineering_team 0.2.0, crewai 1.15.41, python 3.12.12, platform Darwin arm64

> **Small sample:** 10 counted run(s). Pass-rate intervals this wide cannot separate strategies that differ by less than their width; read them as such.

## Results by strategy

| Strategy / agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | 9/10 | 90% (60%–98%) | $0.0036 | $0.0040 | 91s | 0.0 | 12 | 0 |

## Where the time and money went, by strategy

| Strategy / agent | Sessions / run | Model calls / run | Prompt tokens / run | Cached share | Mean cost / run | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| single | 1.0 | 15.9 | 91,991 | 94% | $0.0036 | 91s |

## By task kind and split

| Strategy / agent · kind · split | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single · greenfield · dev | 9/10 | 90% (60%–98%) | $0.0036 | $0.0040 | 91s | 0.0 | 12 | 0 |

## By task

| Task / strategy or agent | Passed | Pass rate (Wilson 95 %) | Mean cost / run | Cost / success | Median time | Repairs / run | Tool failures | Setup failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| csv-validator · single | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 112s | 0.0 | 2 | 0 |
| markdown-html · single | 1/2 | 50% (9%–91%) | $0.0031 | $0.0063 | 71s | 0.0 | 2 | 0 |
| notes-cli · single | 2/2 | 100% (34%–100%) | $0.0029 | $0.0029 | 75s | 0.0 | 1 | 0 |
| todo-web · single | 2/2 | 100% (34%–100%) | $0.0039 | $0.0039 | 92s | 0.0 | 4 | 0 |
| url-shortener · single | 2/2 | 100% (34%–100%) | $0.0041 | $0.0041 | 100s | 0.0 | 3 | 0 |

## Failures (1)

| Task | Strategy / agent | Run | Outcome | Missed criteria | Why |
| --- | --- | --- | --- | --- | --- |
| markdown-html | single | 2 | failed | inline-formatting | criteria not met: inline-formatting |

Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all spend, failed runs included, over the successes; `unknown` means a model had no price.
