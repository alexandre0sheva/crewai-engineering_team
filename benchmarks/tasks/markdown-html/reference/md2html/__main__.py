import sys

from md2html import convert


def main(argv: list[str]) -> int:
    try:
        text = open(argv[0], encoding="utf-8").read() if argv else sys.stdin.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(convert(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
