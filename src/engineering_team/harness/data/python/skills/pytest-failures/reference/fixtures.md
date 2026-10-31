# Fixtures that fix most isolation problems

- `tmp_path`: a fresh directory per test (a `pathlib.Path`); write files there, never in the repo.
- `monkeypatch`: `setenv`/`delenv`, `setattr` on a module, `chdir`; undone after the test.
- `capsys` / `caplog`: captured output and log records.
- `pytest.raises(Error, match="...")`: the exception and its message.
- `@pytest.mark.parametrize("a,b", [(1, 2), (3, 4)])`: one case per row, each reported apart.
- Freeze time by injecting a clock or monkeypatching the function that reads it; avoid `sleep`.
