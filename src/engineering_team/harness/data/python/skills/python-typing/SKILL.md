---
name: python-typing
description: Type hints, or mypy and pyright errors.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Python typing

- Run the checker the project configures (`[tool.mypy]`, `pyrightconfig.json`) on the files you
  touched first, then on the project.
- Fix the type, not the checker: avoid `# type: ignore` and `Any`; when one is unavoidable, give
  the error code (`# type: ignore[attr-defined]`) and a reason.
- `X | None` needs a check before use (`if x is None: ...`); narrowing with `isinstance` or an
  early return is clearer than `cast`.
- With `from __future__ import annotations`, annotations are not evaluated at runtime, so an
  import used only in annotations can sit under `if TYPE_CHECKING:`.
- Prefer `collections.abc` (`Sequence`, `Mapping`, `Iterable`) for parameters and concrete types
  for return values; `TypedDict` or a dataclass beats `dict[str, Any]` for structured data.
