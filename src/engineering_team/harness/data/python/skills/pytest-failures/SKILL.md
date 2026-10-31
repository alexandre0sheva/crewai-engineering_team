---
name: pytest-failures
description: A pytest run fails or errors.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Triage a pytest failure

1. Run only what failed: `pytest path/to/test_file.py::test_name -x -q`, then with `-vv` for the
   full assertion diff. `--lf` reruns the last failures.
2. Read the report from the bottom: the `E` lines are the failure, the frames above lead to it.
   An error in setup (a fixture, an import, `conftest.py`) is not the test's assertion failing.
3. Classify before fixing:
   - `ImportError` / `ModuleNotFoundError`: the package is not installed in this environment, or
     the import path is wrong (`src/` layout needs an editable install or `pythonpath`).
   - Assertion on data: compare expected and actual; decide which side is wrong from the
     specification, not from which is easier to change.
   - Flaky (passes alone, fails in the suite): shared state - module globals, the current
     directory, environment variables, time, ordering. Fix the isolation (`tmp_path`,
     `monkeypatch`), not the assertion.
4. Never edit a test to match broken behaviour; change a test only when the test itself is wrong,
   and say why.
5. Rerun the one test, then the file, then the suite.

`reference/fixtures.md` covers the fixtures most fixes need.
