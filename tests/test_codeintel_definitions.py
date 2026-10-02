from __future__ import annotations

from polyglot_repo import FILES, line_of

from engineering_team.codeintel.definitions import Definition, definitions_in


def _by_name(path: str, text: str | None = None) -> dict[str, Definition]:
    found = definitions_in(path, FILES[path] if text is None else text)
    return {definition.name: definition for definition in found}


def test_python_definitions_have_kind_lines_parent_and_extent() -> None:
    found = _by_name("app/config.py")

    parse = found["parse_config"]
    assert (parse.kind, parse.parent) == ("function", None)
    assert parse.line == line_of("app/config.py", "def parse_config")
    assert parse.end_line == len(FILES["app/config.py"].splitlines())
    assert (found["Settings"].kind, found["Settings"].parent) == ("class", None)
    assert (found["get"].kind, found["get"].parent) == ("method", "Settings")
    assert found["DEFAULT_PATH"].kind == "const"
    assert found["Settings"].end_line == line_of("app/config.py", "return self.data.get")


def test_typescript_definitions_including_arrow_functions_and_methods() -> None:
    found = _by_name("web/src/api.ts")

    assert found["User"].kind == "interface"
    assert found["Client"].kind == "class"
    assert (found["get"].kind, found["get"].parent) == ("method", "Client")
    assert found["fetchUser"].kind == "function"
    assert found["fetchUser"].end_line == line_of("web/src/api.ts", "return { id") + 1
    assert found["formatUser"].kind == "function"
    assert "if" not in found and "return" not in found


def test_go_definitions_methods_carry_their_receiver_type() -> None:
    found = _by_name("svc/server.go")

    assert found["Server"].kind == "struct"
    assert found["ParseConfig"].kind == "function"
    assert (found["Start"].kind, found["Start"].parent) == ("method", "Server")
    assert found["ParseConfig"].end_line == line_of("svc/server.go", "return &Server") + 1


def test_other_languages_use_regex_rules() -> None:
    java = _by_name(
        "Billing.java",
        "public class Billing {\n    public int total(int a) {\n        return a;\n    }\n}\n",
    )
    rust = _by_name("lib.rs", "pub struct Meter;\n\npub fn read(m: &Meter) -> u32 {\n    1\n}\n")
    ruby = _by_name("shop.rb", "class Shop\n  def open\n    1\n  end\nend\n")
    php = _by_name("Shop.php", "<?php\nclass Shop {\n    public function open() {\n    }\n}\n")
    csharp = _by_name("Shop.cs", "public class Shop\n{\n    public void Open()\n    {\n    }\n}\n")

    assert java["Billing"].kind == "class"
    assert (java["total"].kind, java["total"].parent) == ("method", "Billing")
    assert java["total"].end_line == 4
    assert (rust["Meter"].kind, rust["read"].kind) == ("struct", "function")
    assert (ruby["Shop"].kind, ruby["open"].kind, ruby["open"].end_line) == ("class", "method", 4)
    assert (php["Shop"].kind, php["open"].kind) == ("class", "method")
    assert (csharp["Shop"].kind, csharp["Open"].kind) == ("class", "method")


def test_a_python_file_with_a_syntax_error_still_yields_regex_definitions() -> None:
    found = _by_name("broken.py", "def ok():\n    return 1\n\ndef broken(:\n")

    assert found["ok"].kind == "function"


def test_unsupported_files_have_no_definitions() -> None:
    assert definitions_in("notes.txt", "def x(): pass") == []
