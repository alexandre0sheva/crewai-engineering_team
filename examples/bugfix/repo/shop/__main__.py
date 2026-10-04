import argparse
import json
import sys

from shop.cart import Cart
from shop.report import report


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = json.load(handle)
    except OSError:
        print(f"error: cannot read {path}", file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError:
        print(f"error: {path} is not valid JSON", file=sys.stderr)
        raise SystemExit(2) from None
    cart = Cart()
    for line in lines:
        cart.add(line["name"], line["price"], line.get("qty", 1))
    return cart


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shop")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("report").add_argument("--file", required=True)
    args = parser.parse_args(argv)
    print(report(load(args.file)))


if __name__ == "__main__":
    main()
