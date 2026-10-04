# Contributing

Thanks for helping. This project is a CrewAI-based AI engineering team; contributions are
welcome as issues, discussions, and pull requests. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities privately (see [SECURITY.md](SECURITY.md)).

## Development setup

Requirements: Python 3.11–3.13 (3.14 once CrewAI supports it) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev --extra browser   # the extra adds Playwright for the browser tools
```

The repository's `.env` (API keys) is never needed for development: the test suite is
hermetic and must stay that way.

## The gate

Every change must pass the same checks CI runs:

```bash
uv sync --locked --group dev --extra browser
uv run ruff check .
uv run ruff format --check .      # `uv run ruff format .` fixes formatting
uv run mypy
uv run pytest -q
uv run python scripts/check_docs.py   # env vars documented, one row per setting, links resolve
uv build
```

Test rules:

- Tests run offline: no network, no LLM calls, no reading your `.env` or `~/.config`.
  `tests/conftest.py` isolates the environment, framework storage, and the working directory.
- Tests that need Docker, a real browser, or a live model must be marked (`docker`, `browser`,
  `live`) and are skipped by default (`tests/conftest.py`): `live` runs only with
  `ENGINEERING_LIVE_TESTS=1` and a provider API key in the environment, `docker` only when
  `docker info` succeeds (they pull small `alpine` and `python:3.12-alpine` images on first use), `browser` only when Playwright can launch Chromium (`uv run playwright
  install chromium`) or an installed Google Chrome. A guard in the same file
  fails any other test that connects to a non-loopback address.
- Every bug fix gets a regression test that fails without the fix.

### Testing agents offline

`engineering_team.testing` (shipped in the wheel) runs a real CrewAI `Agent` on a scripted model,
so agent flows are deterministic and free:

```python
from engineering_team.testing import ScriptedLLM, ToolCall, run_agent_task

llm = ScriptedLLM([
    ToolCall("Write Project File", {"path": "app.py", "content": "print('hi')\n"}),
    ToolCall("Run Project Command", {"command": "python app.py"}),
    "Wrote and ran app.py.",  # a plain string is the final answer
])
answer = run_agent_task(ctx, llm)  # ctx: a RunContext over a temp workspace
llm.assert_exhausted()
```

- Script items are strings (final answer), `ToolCall`s (a list means parallel calls), callables
  `(messages, tools) -> reply` that react to the conversation, or `Turn(reply, prompt_tokens=,
  completion_tokens=)` to script token usage. Running out of script raises `ScriptExhausted`.
- `ScriptedLLM(..., native_tools=False)` exercises CrewAI's text ReAct protocol instead of native
  function calling. `llm.calls` records every prompt and the tool schemas sent.
- `build_agent`/`build_task`/`run_agent_task` in `engineering_team.testing.fakes` bind the real
  tools to a `RunContext`; the `make_context` fixture in `tests/conftest.py` builds one.
  `tests/test_fake_llm_integration.py` is the reference example.

### Benchmarks

`benchmarks/` holds the benchmark tasks and their hidden checks (repository only; see
[docs/BENCHMARKS.md](docs/BENCHMARKS.md) for the method). `uv run engineering-team bench run --fake`
replays each task's reference solution offline and must pass; with `--fake-solution broken` it must
fail. `tests/test_bench_fake.py` runs both halves (about two minutes in total) so a check that stops
telling good from bad fails CI. Live runs cost real money and are never part of the gate.

Before changing CrewAI-specific code, follow the research steps in [AGENTS.md](AGENTS.md)
(installed version, PyPI, changelog, live docs): CrewAI changes quickly.

## Working through the implementation plan

Work for release 0.2.0 is organised as numbered tasks in
[docs/IMPLEMENTATION_PLAN_0.2.0.md](docs/IMPLEMENTATION_PLAN_0.2.0.md). A helper prints what you need:

```bash
uv run python scripts/plan_task.py list        # all tasks and their status
uv run python scripts/plan_task.py next        # first runnable task
uv run python scripts/plan_task.py show 7      # rules + task 7 + dependency status
uv run python scripts/plan_task.py done 7      # mark task 7 done after the gate passes
```

## Scope of changes and commits

- One task or one logical change per pull request/commit; keep unrelated formatting changes
  out (the formatter excludes Markdown on purpose).
- Imperative, descriptive commit subjects (`feat:`, `fix:`, `docs:`, `chore:`, `test:` prefixes
  are welcome).
- Add a [CHANGELOG](CHANGELOG.md) entry under `[Unreleased]` for every user-visible change
  (Added / Changed / Fixed / Security; one line each).
- Do not commit secrets, `.env`, generated workspaces (`workspace/`), or run records.

## Documentation map

One home per fact: update the canonical file and link to it from elsewhere instead of copying.

| Topic | Canonical file | Notes |
|-------|----------------|-------|
| Pitch, install, 5-minute quickstart, mode overview, links | `README.md` | Short. No env tables, no architecture, no CLI reference. |
| CLI/UI usage, workflows per mode, writing a good request | `docs/USAGE.md` | |
| Settings, env vars, config file, model presets, budgets | `docs/CONFIGURATION.md` | The README has no environment table. |
| Design, modules, data flow, parallelism, run state | `docs/ARCHITECTURE.md` | |
| Tool catalogue: every agent tool, its group, limits, safety notes, which teammates get it | `docs/TOOLS.md` (every new tool adds its row) | Single home for tool facts; TEAM.md only references groups. |
| Execution boundary, Docker sandbox, threat model | `docs/SAFETY.md` | `SECURITY.md` = vulnerability reporting only. |
| Teammates and how to add one; default tool groups per teammate | `docs/TEAM.md` | |
| Task board / dashboard behaviour and card lifecycle | `docs/ARCHITECTURE.md` (board section) + `docs/USAGE.md` (viewing progress) | |
| Runnable examples and their sample run reports | `examples/` | Reports are regenerated offline by `scripts/make_example_reports.py`. |
| Benchmark method + results | `docs/BENCHMARKS.md` | README links only. |
| Dev workflow, doc map, release process | `CONTRIBUTING.md` | This table. |
| History | `CHANGELOG.md` | |
| Assistant instructions | `AGENTS.md` | Only update version facts; do not duplicate project docs into it. |

## Releasing

Releases are tagged from `main` and published by `.github/workflows/release.yml`. Nothing below is run
for you.

1. **Make the release commit.** The version in `pyproject.toml` is the version to ship. In
   [CHANGELOG.md](CHANGELOG.md) move the `[Unreleased]` entries under `## [X.Y.Z] — YYYY-MM-DD`, add a
   migration note if users must change something, leave an empty `## [Unreleased]` above it, and update the
   compare links at the end. `uv run python scripts/release_notes.py --check` says whether the changelog is
   ready; `uv run python scripts/release_notes.py X.Y.Z` prints the notes the GitHub Release will carry.
2. **Run the gate** (above) and wait for CI to be green on that commit, on every platform.
3. **Tag and push.**

   ```bash
   git tag -a vX.Y.Z -m "X.Y.Z"
   git push origin main --tags
   ```

   The workflow refuses a tag that does not match the `pyproject.toml` version or has no changelog section,
   builds the sdist and wheel, installs the wheel in a clean environment on Linux and macOS (Python 3.11 and
   3.13) and runs `engineering-team --help` from an unrelated directory, then creates the GitHub Release with
   the wheel, the sdist and the changelog section.
4. **PyPI (optional, off by default).** Create the project on PyPI, add this repository's `release.yml` as a
   [trusted publisher](https://docs.pypi.org/trusted-publishers/) (no token is stored), create a `pypi`
   environment in the repository settings, and set the repository variable `PUBLISH_TO_PYPI` to `true`. The
   next tag then also publishes to PyPI; until then the last job is skipped.
5. **Widening Python.** Python 3.14 is out of range because CrewAI declares `<3.14`. When a CrewAI release
   supports it, change `requires-python`, the classifiers, `target-version` and `python_version` (Ruff and
   mypy), the CI and release matrices and the README badge in one commit.
