from __future__ import annotations

import pytest

from engineering_team.tools.scope import WriteScope

SCOPE = WriteScope(
    allow=("src/api/**", "tests/api/*.py", "/README.md", "*.sql", "build/"),
    deny=("src/api/generated/**",),
)


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("src/api/handler.py", True),
        ("src/api/deep/nested/handler.py", True),
        ("src/api/generated/client.py", False),  # deny beats allow
        ("src/web/page.tsx", False),
        ("tests/api/test_handler.py", True),
        ("tests/api/sub/test_handler.py", False),  # ``*`` does not cross directories
        ("README.md", True),
        ("docs/README.md", False),  # a leading slash anchors the pattern to the root
        ("db/schema.sql", True),  # no slash: matches at any depth, like .gitignore
        ("build/out/app.js", True),  # a directory pattern covers everything below it
        ("src/build.py", False),
    ],
)
def test_gitignore_style_globs(path: str, allowed: bool) -> None:
    assert SCOPE.permits(path) is allowed


def test_an_empty_allow_list_permits_nothing() -> None:
    assert WriteScope(allow=()).permits("anything.txt") is False


def test_the_denial_names_the_owned_paths() -> None:
    message = SCOPE.denial("src/web/page.tsx")

    assert "src/web/page.tsx" in message
    assert "src/api/**" in message
    assert "src/api/generated/**" in message
