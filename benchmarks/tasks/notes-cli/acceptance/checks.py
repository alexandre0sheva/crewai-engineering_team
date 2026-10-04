import json
import os

from engineering_team.bench.checklib import expect, main, python


def notes(ws, store, *args):
    env = {**os.environ, "NOTES_FILE": str(store)}
    return python(ws, "-m", "notes", *args, env=env)


def lines(result):
    return result.stdout.strip().splitlines()


def check_add_and_list(ws):
    store = ws / "data.json"
    first = notes(ws, store, "add", "buy milk", "--tag", "home", "--tag", "food")
    expect(
        first.returncode == 0 and first.stdout.strip() == "Added note 1",
        f"add: {first.stdout!r} {first.stderr[-200:]}",
    )
    expect(
        notes(ws, store, "add", "write report").stdout.strip() == "Added note 2",
        "second add must print 'Added note 2'",
    )
    listing = notes(ws, store, "list")
    expect(
        lines(listing) == ["1: buy milk [home,food]", "2: write report"],
        f"list output was {lines(listing)}",
    )
    empty = notes(ws, ws / "none.json", "list")
    expect(
        empty.returncode == 0 and empty.stdout.strip() == "No notes.",
        f"empty list printed {empty.stdout!r}",
    )


def check_ids_not_reused(ws):
    store = ws / "data.json"
    for text in ("a", "b", "c"):
        notes(ws, store, "add", text)
    expect(
        notes(ws, store, "delete", "2").stdout.strip() == "Deleted note 2",
        "delete must print 'Deleted note 2'",
    )
    again = notes(ws, store, "add", "d")
    expect(
        again.stdout.strip() == "Added note 4",
        f"after deleting 2 the next id must be 4, got {again.stdout!r}",
    )
    expect(
        lines(notes(ws, store, "list")) == ["1: a", "3: c", "4: d"], "list after delete is wrong"
    )


def check_tag_filter_and_search(ws):
    store = ws / "data.json"
    notes(ws, store, "add", "Buy MILK", "--tag", "home")
    notes(ws, store, "add", "Pay rent", "--tag", "home", "--tag", "money")
    notes(ws, store, "add", "call mom")
    expect(
        lines(notes(ws, store, "list", "--tag", "home"))
        == ["1: Buy MILK [home]", "2: Pay rent [home,money]"],
        "list --tag home is wrong",
    )
    expect(
        notes(ws, store, "list", "--tag", "nothing").stdout.strip() == "No notes.",
        "unknown tag must print 'No notes.'",
    )
    expect(
        lines(notes(ws, store, "search", "milk")) == ["1: Buy MILK [home]"],
        "search must ignore case",
    )
    expect(
        notes(ws, store, "search", "zzz").stdout.strip() == "No notes.",
        "search without matches must print 'No notes.'",
    )


def check_delete_and_errors(ws):
    store = ws / "data.json"
    notes(ws, store, "add", "only")
    missing = notes(ws, store, "delete", "9")
    expect(missing.returncode == 1, f"unknown id must exit 1, exit was {missing.returncode}")
    expect("No such note: 9" in missing.stderr, f"stderr was {missing.stderr!r}")
    expect(notes(ws, store, "delete", "1").returncode == 0, "deleting an existing note must exit 0")
    expect(notes(ws, store, "frobnicate").returncode == 2, "an unknown command must exit 2")
    expect(notes(ws, store, "add").returncode == 2, "add without text must exit 2")
    expect(notes(ws, store).returncode in (0, 2), "no command must not crash")


def check_storage_file(ws):
    store = ws / "elsewhere" / "n.json"
    store.parent.mkdir()
    notes(ws, store, "add", "persisted", "--tag", "x")
    expect(store.is_file(), "NOTES_FILE was not written")
    try:
        data = json.loads(store.read_text())
    except ValueError:
        raise AssertionError("the notes file is not valid JSON") from None
    expect("persisted" in json.dumps(data), "the note text is not in the JSON file")
    env = {k: v for k, v in os.environ.items() if k != "NOTES_FILE"}
    from engineering_team.bench.checklib import python as run_python

    run_python(ws, "-m", "notes", "add", "default location", env=env)
    expect((ws / "notes.json").is_file(), "without NOTES_FILE the notes must go to ./notes.json")


if __name__ == "__main__":
    main()
