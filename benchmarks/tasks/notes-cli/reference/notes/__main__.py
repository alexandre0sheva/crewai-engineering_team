import argparse
import sys

from notes import store


def line(note: dict) -> str:
    tags = f" [{','.join(note['tags'])}]" if note["tags"] else ""
    return f"{note['id']}: {note['text']}{tags}"


def show(notes: list[dict]) -> None:
    print("\n".join(line(n) for n in notes) if notes else "No notes.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notes")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("text")
    add.add_argument("--tag", action="append", default=[])
    listing = commands.add_parser("list")
    listing.add_argument("--tag")
    commands.add_parser("search").add_argument("word")
    commands.add_parser("delete").add_argument("id", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add":
        print(f"Added note {store.add(args.text, args.tag)['id']}")
    elif args.command == "list":
        show(store.find(tag=args.tag))
    elif args.command == "search":
        show(store.find(word=args.word))
    elif store.remove(args.id):
        print(f"Deleted note {args.id}")
    else:
        print(f"No such note: {args.id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
