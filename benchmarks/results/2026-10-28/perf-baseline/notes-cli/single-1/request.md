# Notes CLI

Build a small command-line notes tool in Python 3.11 or newer, using only the standard library
(no third-party packages).

It is run from the project root as `python -m notes COMMAND ...`, so put a top-level package
`notes/` (with a `__main__.py`) directly in the project root, not under `src/`.

Notes are stored in a JSON file. Its path comes from the environment variable `NOTES_FILE`; when
that is not set, use `notes.json` in the current directory. A missing file means "no notes yet";
the file is created on the first write.

## Commands

- `add TEXT [--tag TAG]...` adds a note and prints `Added note N`, where N is its id. `--tag` may
  be repeated. Ids start at 1, grow by one for every note added, and are never reused, even after
  a delete.
- `list [--tag TAG]` prints one line per note, oldest first, as `N: TEXT`, followed by
  ` [tag1,tag2]` when the note has tags (in the order they were given). With `--tag`, only notes
  that have that tag are shown. When there is nothing to show it prints `No notes.`
- `search WORD` prints the notes whose text contains WORD, ignoring case, in the same line format
  as `list`, or `No notes.` when none match.
- `delete N` removes the note with id N and prints `Deleted note N`. For an unknown id it prints
  `No such note: N` to stderr and exits with status 1.

Success exits with status 0. Any other misuse (an unknown command, missing arguments) exits with
status 2 and a usage message on stderr.

Add a few tests and a short README describing usage.
