---
name: clippy
description: Clippy lints or compiler warnings.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Clippy and warnings

- `cargo clippy --all-targets -- -D warnings` is what CI usually runs; `cargo fmt --check` too.
- Fix the code the lint points at; `#[allow(clippy::lint_name)]` only on the smallest item, with a
  comment saying why.
- Common ones: `needless_borrow`, `redundant_clone`, `unwrap_used` in library code (return an
  error instead), `too_many_arguments` (a struct of options).
