import argparse
import sys
from datetime import datetime

from cronlite import next_run


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cronlite")
    parser.add_argument("expression")
    parser.add_argument("--after")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        moment = datetime.fromisoformat(args.after) if args.after else datetime.now()
        for _ in range(args.count):
            moment = next_run(args.expression, moment)
            print(moment.strftime("%Y-%m-%dT%H:%M"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
