import sys
from datetime import datetime

from engineering_team.bench.checklib import expect, main, python


def load(ws):
    sys.path.insert(0, str(ws))
    from cronlite import next_run

    return next_run


def runs(ws, expression, after, count=1):
    next_run = load(ws)
    moment = datetime.fromisoformat(after)
    found = []
    for _ in range(count):
        moment = next_run(expression, moment)
        expect(isinstance(moment, datetime), "next_run must return a datetime")
        expect(
            moment.second == 0 and moment.microsecond == 0, f"{moment} must have seconds at zero"
        )
        found.append(moment.strftime("%Y-%m-%dT%H:%M"))
    return found


def same(ws, expression, after, expected):
    got = runs(ws, expression, after, len(expected))
    expect(got == expected, f"{expression!r} after {after}: expected {expected}, got {got}")


def check_simple_schedules(ws):
    same(ws, "* * * * *", "2025-03-10T10:07", ["2025-03-10T10:08", "2025-03-10T10:09"])
    same(
        ws,
        "*/15 * * * *",
        "2025-03-10T10:07",
        ["2025-03-10T10:15", "2025-03-10T10:30", "2025-03-10T10:45", "2025-03-10T11:00"],
    )
    same(ws, "30 9 * * *", "2025-03-10T09:30", ["2025-03-11T09:30", "2025-03-12T09:30"])
    same(ws, "0 0 1 * *", "2025-03-10T00:00", ["2025-04-01T00:00", "2025-05-01T00:00"])
    same(ws, "5 4 * * *", "2025-03-10T04:04:59", ["2025-03-10T04:05"])


def check_ranges_steps_lists(ws):
    same(
        ws,
        "0,30 8-10 * * *",
        "2025-03-10T08:00",
        ["2025-03-10T08:30", "2025-03-10T09:00", "2025-03-10T09:30", "2025-03-10T10:00"],
    )
    same(
        ws,
        "10-20/5 * * * *",
        "2025-03-10T10:00",
        ["2025-03-10T10:10", "2025-03-10T10:15", "2025-03-10T10:20", "2025-03-10T11:10"],
    )
    same(
        ws,
        "0 */6 * * *",
        "2025-03-10T07:00",
        ["2025-03-10T12:00", "2025-03-10T18:00", "2025-03-11T00:00"],
    )
    same(ws, "0 0 1,15 * *", "2025-03-10T00:00", ["2025-03-15T00:00", "2025-04-01T00:00"])
    same(ws, "0 0 * 1-3 *", "2025-03-31T00:00", ["2026-01-01T00:00"])
    same(
        ws,
        "0 0 1-10/3 * *",
        "2025-03-01T00:00",
        ["2025-03-04T00:00", "2025-03-07T00:00", "2025-03-10T00:00", "2025-04-01T00:00"],
    )


def check_calendar_rollover(ws):
    same(ws, "0 0 31 * *", "2025-01-31T00:00", ["2025-03-31T00:00", "2025-05-31T00:00"])
    same(ws, "59 23 31 12 *", "2025-12-31T23:59", ["2026-12-31T23:59"])
    same(ws, "0 0 29 2 *", "2025-03-01T00:00", ["2028-02-29T00:00", "2032-02-29T00:00"])
    same(ws, "0 12 * * 0", "2025-03-10T00:00", ["2025-03-16T12:00", "2025-03-23T12:00"])
    same(ws, "0 12 * * 6", "2025-03-15T12:00", ["2025-03-22T12:00"])
    same(ws, "0 0 * 2 *", "2025-02-28T00:00", ["2026-02-01T00:00"])


def check_dom_dow_rule(ws):
    # 2025-03-10 is a Monday. Day of month 15 OR Sunday.
    same(
        ws,
        "0 0 15 * 0",
        "2025-03-10T00:00",
        ["2025-03-15T00:00", "2025-03-16T00:00", "2025-03-23T00:00"],
    )
    # Only day of week restricted; only day of month restricted.
    same(ws, "0 0 * * 1", "2025-03-10T00:00", ["2025-03-17T00:00"])
    same(ws, "0 0 20 * *", "2025-03-10T00:00", ["2025-03-20T00:00"])
    # 1st of the month OR Friday (2025-04-04 is a Friday, 2025-04-01 a Tuesday).
    same(ws, "0 0 1 * 5", "2025-03-31T00:00", ["2025-04-01T00:00", "2025-04-04T00:00"])


def check_validation(ws):
    next_run = load(ws)
    start = datetime(2025, 3, 10, 10, 0)
    for bad in (
        "",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 13 *",
        "* * * * 7",
        "5-1 * * * *",
        "*/0 * * * *",
        "a * * * *",
        "1- * * * *",
        "*/ * * * *",
        "5/10 * * * *",
        ", * * * *",
    ):
        try:
            next_run(bad, start)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} must raise ValueError")
    try:
        next_run("0 0 30 2 *", start)
    except ValueError:
        pass
    else:
        raise AssertionError("an expression that never fires must raise ValueError")


def check_cli(ws):
    ok = python(ws, "-m", "cronlite", "*/20 * * * *", "--after", "2025-03-10T10:07", "--count", "3")
    expect(ok.returncode == 0, f"exit {ok.returncode}: {ok.stderr[-200:]}")
    expect(
        ok.stdout.split() == ["2025-03-10T10:20", "2025-03-10T10:40", "2025-03-10T11:00"],
        f"output was {ok.stdout!r}",
    )
    one = python(ws, "-m", "cronlite", "0 0 1 1 *", "--after", "2025-06-01T00:00")
    expect(one.stdout.split() == ["2026-01-01T00:00"], f"default count is 1: {one.stdout!r}")
    expect(
        python(ws, "-m", "cronlite", "bogus", "--after", "2025-03-10T10:07").returncode == 2,
        "a bad expression exits 2",
    )
    expect(
        python(ws, "-m", "cronlite", "* * * * *", "--after", "yesterday").returncode == 2,
        "a bad --after exits 2",
    )
    now = python(ws, "-m", "cronlite", "* * * * *")
    expect(
        now.returncode == 0 and len(now.stdout.split()) == 1,
        "without --after the command uses the current time",
    )


if __name__ == "__main__":
    main()
