"""A small Python + TypeScript + Go repository for the code intelligence tests.

The files are written from this module (not stored as files) so pytest does not collect the
fixture's own ``test_*.py`` files. ``parse_config`` is used by ``app/main.py``, ``app/cli.py``,
and ``tests/test_config.py``: "who breaks if I change it?" has a known answer.
"""

from __future__ import annotations

from pathlib import Path

FILES: dict[str, str] = {
    "app/__init__.py": "",
    "app/config.py": '''"""Configuration loading."""

import json

DEFAULT_PATH = "config.json"


class Settings:
    def __init__(self, data):
        self.data = data

    def get(self, key):
        return self.data.get(key)


def parse_config(path=DEFAULT_PATH):
    # TODO(ana): validate the schema
    with open(path) as handle:
        return Settings(json.load(handle))
''',
    "app/main.py": """from app.config import parse_config


def run():
    settings = parse_config("prod.json")
    return settings.get("name")
""",
    "app/cli.py": """from app import config


def load():
    return config.parse_config()
""",
    "tests/test_config.py": """from app.config import parse_config


def test_parse_config(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{}")
    assert parse_config(str(path)).data == {}
""",
    "tests/test_cli.py": """from app.cli import load


def test_load():
    assert load is not None
""",
    "pyproject.toml": """[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2.31", "pydantic==2.7.1"]

[project.optional-dependencies]
cli = ["click>=8"]

[dependency-groups]
dev = ["pytest>=8"]
""",
    "web/src/api.ts": """export interface User {
  id: number;
}

export class Client {
  async get(path: string): Promise<User> {
    return fetchUser(path);
  }
}

export function fetchUser(id: string): User {
  // FIXME: handle errors
  return { id: Number(id) };
}

export const formatUser = (user: User): string => `user ${user.id}`;
""",
    "web/src/ui.ts": """import { fetchUser, formatUser } from "./api";

export function render(id: string): string {
  return formatUser(fetchUser(id));
}
""",
    "web/src/api.spec.ts": """import { fetchUser } from "./api";

describe("fetchUser", () => {
  it("parses ids", () => {
    expect(fetchUser("1").id).toBe(1);
  });
});
""",
    "web/package.json": """{
  "name": "web",
  "dependencies": {"left-pad": "^1.3.0", "react": "18.2.0"},
  "devDependencies": {"typescript": "~5.4.0"}
}
""",
    "go.mod": """module example.com/svc

go 1.22

require (
	github.com/gorilla/mux v1.8.1
	golang.org/x/text v0.14.0 // indirect
)
""",
    "svc/server.go": """package svc

// HACK: temporary port override
type Server struct {
	Port int
}

func ParseConfig(path string) (*Server, error) {
	return &Server{Port: 80}, nil
}

func (s *Server) Start() error {
	return nil
}
""",
    "svc/server_test.go": """package svc

import "testing"

func TestParseConfig(t *testing.T) {
	if _, err := ParseConfig("x"); err != nil {
		t.Fatal(err)
	}
}
""",
    "cmd/main.go": """package main

import (
	"fmt"

	"example.com/svc/svc"
)

func main() {
	server, _ := svc.ParseConfig("x")
	fmt.Println(server)
}
""",
}


def write_polyglot(root: Path) -> None:
    for relative, content in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def line_of(path: str, needle: str) -> int:
    """The 1-based line of the first line of ``path`` that contains ``needle``."""

    for number, line in enumerate(FILES[path].splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not in {path}")
