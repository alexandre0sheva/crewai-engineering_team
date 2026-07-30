<!-- ENGINEERING_TEAM_PROFILE: smoke -->
# Tiny Notes CLI

This is the bundled low-cost smoke project. Replace this file with your real
product request and remove the smoke-profile marker above when you are ready to
build a full MVP.

## Product

- Name: Tiny Notes CLI
- Problem: A developer wants to capture and review short notes without opening
  a browser or configuring a database.
- Primary user: One local developer.
- Primary user journey: Add a note from the terminal, then list saved notes.

## MVP scope

- Must have:
  - `add <text>` command that persists a timestamped note.
  - `list` command that prints saved notes in creation order.
  - Clear validation when note text is empty.
  - Automated tests for add, list, persistence, and invalid input.
- Explicitly out of scope:
  - Web or graphical interface.
  - Authentication, networking, synchronization, search, editing, or deletion.
  - Packaging or deployment beyond local execution.
- Preferred platform or stack: Python 3.12 standard library only.

## Acceptance criteria

1. `python notes.py add "buy milk"` stores a note in a local JSON file and exits
   successfully.
2. `python notes.py list` displays the stored text and timestamp.
3. Data remains available to a later process using the same data file.
4. Empty note text returns a non-zero exit code and a useful message.
5. The documented test command passes.

## Constraints

- Data/privacy: Store data only in the generated project directory.
- Integrations: None.
- Deployment target: Local macOS/Linux terminal.
- Time, cost, or dependency constraints: Keep implementation deliberately tiny:
  standard library only, no web research, no remote documentation lookup, and
  no speculative features. Prefer one implementation module, one test module,
  README, and the required engineering documents.
