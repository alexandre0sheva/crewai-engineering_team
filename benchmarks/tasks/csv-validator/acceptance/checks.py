import json

from engineering_team.bench.checklib import expect, main, python

SCHEMA = {
    "columns": [
        {"name": "id", "type": "int", "required": True, "unique": True, "min": 1},
        {"name": "email", "type": "string", "pattern": r"[^@\s]+@[^@\s]+\.[a-z]+"},
        {"name": "age", "type": "int", "min": 0, "max": 150},
        {"name": "joined", "type": "date"},
        {"name": "status", "type": "string", "allowed": ["active", "closed"]},
        {"name": "score", "type": "float"},
        {"name": "vip", "type": "bool"},
    ]
}
HEADER = "id,email,age,joined,status,score,vip\n"


def validate(ws, rows, schema=SCHEMA, header=HEADER):
    (ws / "schema.json").write_text(json.dumps(schema))
    (ws / "data.csv").write_text(header + rows)
    return python(ws, "-m", "csvcheck", "schema.json", "data.csv")


def error_lines(result):
    return [x for x in result.stdout.splitlines() if x.startswith(("row ", "header:"))]


def has_error(result, row, column):
    prefix = f"row {row}, column {column}:"
    return any(x.startswith(prefix) for x in error_lines(result))


def check_valid_file(ws):
    rows = (
        "1,a@example.com,30,2024-02-29,active,1.5,true\n"
        "2,,,,,,\n"
        "3,b@example.org,150,2023-12-31,closed,-2,false\n"
    )
    result = validate(ws, rows)
    expect(result.returncode == 0, f"exit {result.returncode}: {result.stdout[-300:]}")
    expect(result.stdout.strip().splitlines()[-1] == "OK: 3 rows", f"output was {result.stdout!r}")


def check_type_errors(ws):
    rows = (
        "1,a@example.com,abc,2024-01-01,active,1.5,true\n"
        "2,a@example.com,30,2024-13-01,active,1.5,true\n"
        "3,a@example.com,30,2024-01-01,active,x,true\n"
        "4,a@example.com,30,2024-01-01,active,1.5,yes\n"
        "five,a@example.com,30,2024-01-01,active,1.5,true\n"
        "6,a@example.com,3.5,2024-01-01,active,1.5,true\n"
    )
    result = validate(ws, rows)
    expect(result.returncode == 1, f"exit must be 1, was {result.returncode}")
    for row, column in ((1, "age"), (2, "joined"), (3, "score"), (4, "vip"), (5, "id"), (6, "age")):
        expect(
            has_error(result, row, column),
            f"missing the error for row {row}, column {column}: {result.stdout}",
        )
    expect(len(error_lines(result)) == 6, f"expected exactly 6 errors, got {error_lines(result)}")
    expect("FAILED: 6 error(s)" in result.stdout, f"summary line missing: {result.stdout!r}")


def check_required_and_unique(ws):
    rows = (
        "1,a@example.com,30,2024-01-01,active,1,true\n"
        ",a@example.com,30,2024-01-01,active,1,true\n"
        "1,a@example.com,30,2024-01-01,active,1,true\n"
        "2,,,,,,\n"
        "2,,,,,,\n"
    )
    result = validate(ws, rows)
    expect(has_error(result, 2, "id"), f"empty required id must be reported: {result.stdout}")
    expect(
        has_error(result, 3, "id"), f"the repeated id on row 3 must be reported: {result.stdout}"
    )
    expect(not has_error(result, 1, "id"), "the first occurrence of a unique value is fine")
    expect(has_error(result, 5, "id"), "the repeated id on row 5 must be reported")
    expect(not has_error(result, 4, "email"), "an empty optional value is not an error")
    expect(len(error_lines(result)) == 3, f"expected 3 errors, got {error_lines(result)}")


def check_constraints(ws):
    rows = (
        "1,a@example.com,0,2024-01-01,active,1,true\n"
        "2,a@example.com,150,2024-01-01,closed,1,true\n"
        "3,a@example.com,-1,2024-01-01,active,1,true\n"
        "4,a@example.com,151,2024-01-01,active,1,true\n"
        "5,not-an-email,30,2024-01-01,active,1,true\n"
        "6,a@example.com,30,2024-01-01,pending,1,true\n"
        "0,a@example.com,30,2024-01-01,active,1,true\n"
        "7,a@example.com.extra stuff,30,2024-01-01,active,1,true\n"
    )
    result = validate(ws, rows)
    errors = error_lines(result)
    expect(not has_error(result, 1, "age"), "age 0 is within [0, 150]")
    expect(not has_error(result, 2, "age"), "age 150 is within [0, 150]")
    for row, column in (
        (3, "age"),
        (4, "age"),
        (5, "email"),
        (6, "status"),
        (7, "id"),
        (8, "email"),
    ):
        expect(
            has_error(result, row, column),
            f"missing the error for row {row}, column {column}: {errors}",
        )
    expect(len(errors) == 6, f"expected 6 errors, got {errors}")
    boundary = validate(
        ws,
        "1,a@example.com,30,2024-01-01,active,1,true\n",
        schema={"columns": [{"name": "id", "type": "int", "min": 1, "max": 1}]},
        header="id\n",
    )
    expect(boundary.returncode == 0, f"a value equal to min and max must pass: {boundary.stdout}")


def check_header_and_usage(ws):
    result = validate(
        ws,
        "1,2\n",
        schema={"columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "string"}]},
        header="id\n",
    )
    expect(result.returncode == 1, f"a missing header column exits 1, was {result.returncode}")
    expect("header: missing column name" in result.stdout, f"output was {result.stdout!r}")
    extra = validate(
        ws, "1,x\n", schema={"columns": [{"name": "id", "type": "int"}]}, header="id,other\n"
    )
    expect(extra.returncode == 0, "extra CSV columns are ignored")
    expect(
        python(ws, "-m", "csvcheck", "nope.json", "nope.csv").returncode == 2,
        "missing files exit 2",
    )
    (ws / "bad.json").write_text("{not json")
    expect(
        python(ws, "-m", "csvcheck", "bad.json", "data.csv").returncode == 2, "invalid JSON exits 2"
    )
    (ws / "shape.json").write_text('{"cols": []}')
    expect(
        python(ws, "-m", "csvcheck", "shape.json", "data.csv").returncode == 2,
        "a wrong schema shape exits 2",
    )
    expect(python(ws, "-m", "csvcheck").returncode == 2, "no arguments exit 2")


def check_quoted_fields(ws):
    schema = {
        "columns": [
            {"name": "id", "type": "int"},
            {"name": "note", "type": "string", "pattern": "[a-z ,\\n]+"},
        ]
    }
    ok = validate(
        ws, '1,"hello, world"\n2,"two\nlines"\n3,plain\n', schema=schema, header="id,note\n"
    )
    expect(ok.returncode == 0 and "OK: 3 rows" in ok.stdout, f"quoted fields: {ok.stdout!r}")
    bad = validate(ws, '1,"two\nlines"\nx,fine\n', schema=schema, header="id,note\n")
    expect(has_error(bad, 2, "id"), f"the record after a multi-line field is row 2: {bad.stdout!r}")


if __name__ == "__main__":
    main()
