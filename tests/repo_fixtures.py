"""Small existing projects for the adoption tests, written from here so pytest does not collect
the fixtures' own ``test_*.py`` files."""

from __future__ import annotations

import os
from pathlib import Path

from git_helpers import git, require_git

PYTHON_APP: dict[str, str] = {
    "pyproject.toml": """[project]
name = "notes"
version = "0.1.0"
dependencies = []

[project.scripts]
notes = "app.main:main"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
""",
    "requirements.txt": "pytest\n",
    "Makefile": "test:\n\tpytest\n\nlint:\n\truff check .\n\nbuild:\n\tpython -m build\n",
    "app/__init__.py": "",
    "app/store.py": "def add(items, text):\n    return [*items, text]\n",
    "app/main.py": (
        "import argparse\n\nfrom app.store import add\n\n\ndef main():\n"
        "    parser = argparse.ArgumentParser()\n    parser.add_argument('text')\n"
        "    print(add([], parser.parse_args().text))\n"
    ),
    "tests/test_store.py": (
        "from app.store import add\n\n\ndef test_add():\n    assert add([], 'a') == ['a']\n"
    ),
    "README.md": "# Notes\n\nA tiny notes app.\n",
    "CONTRIBUTING.md": "Run `make test` before sending a change.\n",
    ".editorconfig": "root = true\n",
    ".github/workflows/ci.yml": "name: ci\non: [push]\n",
}

NODE_APP: dict[str, str] = {
    "package.json": """{
  "name": "shop",
  "version": "1.0.0",
  "main": "src/index.js",
  "bin": {"shop": "bin/shop.js"},
  "scripts": {
    "test": "jest",
    "lint": "eslint .",
    "build": "tsc -p .",
    "start": "node src/index.js"
  },
  "devDependencies": {"jest": "^29.0.0", "eslint": "^9.0.0"}
}
""",
    "package-lock.json": "{}\n",
    "src/index.js": "const http = require('http');\nhttp.createServer(() => {});\n",
    "bin/shop.js": "#!/usr/bin/env node\n",
    "src/cart.js": "exports.total = (items) => items.length;\n",
    "src/cart.test.js": "test('total', () => {});\n",
    ".eslintrc.json": "{}\n",
    ".prettierrc": "{}\n",
    "README.md": "# Shop\n",
}


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def make_repo(root: Path, files: dict[str, str] | None = None, *, branch: str = "main") -> Path:
    """A Git repository on ``branch`` holding ``files`` in one commit."""

    require_git()
    write_tree(root, PYTHON_APP if files is None else files)
    git(root, "init", "-q", "-b", branch)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Initial import")
    return root


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under ``root`` except Git's and the controller's own state: bytes and mtime."""

    found: dict[str, tuple[bytes, int]] = {}
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".engineering-team")]
        for name in names:
            path = Path(current) / name
            found[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found
