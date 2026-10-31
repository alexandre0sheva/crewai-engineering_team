---
name: python-packaging
description: Dependencies, pyproject.toml, entry points, builds.
teammates: [backend_engineer, frontend_engineer, generalist_engineer, devops_engineer]
---
# Python packaging

- Read `pyproject.toml` first; the build backend is `[build-system]` and the tool in use is told by
  the lockfile: `uv.lock` (uv), `poetry.lock` (Poetry), `pdm.lock`, or `requirements*.txt` (pip).
- Add a dependency with the project's own tool (`uv add pkg`, `poetry add pkg`) so the lockfile
  stays in step; never hand-edit a lockfile. Development tools go in the dev group or extra.
- Bound versions the way the file already does (`>=` lower bounds are usual); do not pin exact
  versions in a library.
- A command-line program is `[project.scripts] name = "package.module:function"`.
- `src/` layout: tests import the installed package, so install it editable (`uv sync`,
  `pip install -e .`) before running them.
- Check a build with `uv build` or `python -m build` and look at what the wheel contains.
