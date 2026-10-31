---
name: cargo-test
description: Rust tests with cargo.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Rust tests

- `cargo test` for everything, `cargo test name_filter` for matching tests,
  `cargo test -- --nocapture` to see output, `-p crate` in a workspace.
- Unit tests sit in `#[cfg(test)] mod tests` beside the code; integration tests in `tests/` see only
  the public API.
- `#[should_panic(expected = "...")]` for panics; return `Result<(), E>` from a test to use `?`.
- Compile errors come first: read the first error, not the last; borrow-checker errors usually
  want a shorter borrow or an owned value, not a `clone()` everywhere.
