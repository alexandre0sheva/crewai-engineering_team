import csv
import json
import sys

from csvcheck.rules import check_value


def load_schema(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        schema = json.load(handle)
    columns = schema["columns"] if isinstance(schema, dict) else None
    if not isinstance(columns, list) or not all(isinstance(c, dict) and "name" in c and "type" in c for c in columns):
        raise ValueError('the schema must be {"columns": [{"name": ..., "type": ...}]}')
    return columns


def validate(columns: list[dict], rows: list[dict]) -> list[str]:
    errors, seen = [], {c["name"]: set() for c in columns if c.get("unique")}
    for number, row in enumerate(rows, start=1):
        for column in columns:
            name, value = column["name"], row.get(column["name"]) or ""
            if value == "":
                if column.get("required"):
                    errors.append(f"row {number}, column {name}: a value is required")
                continue
            problems = check_value(column, value)
            if column.get("unique"):
                if value in seen[name]:
                    problems.append(f"{value!r} is not unique")
                seen[name].add(value)
            errors += [f"row {number}, column {name}: {p}" for p in problems]
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m csvcheck SCHEMA.json DATA.csv", file=sys.stderr)
        return 2
    try:
        columns = load_schema(argv[0])
        with open(argv[1], encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            rows = list(reader)
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors = [f"header: missing column {c['name']}" for c in columns if c["name"] not in header]
    if not errors:
        errors = validate(columns, rows)
    if not errors:
        print(f"OK: {len(rows)} rows")
        return 0
    print("\n".join(errors))
    print(f"FAILED: {len(errors)} error(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
