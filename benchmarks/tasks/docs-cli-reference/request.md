# Document the tasktool command line

People keep asking how `python -m tasktool` works. Please write a reference for it in
`docs/cli.md`: every command (`add`, `list`, `done`, `export`) and every option and argument,
including the global `--file`, each with what it does and its default where it has one. Give at
least one example command per command, written as a line that starts with `python -m tasktool`;
run in an empty directory one after the other, top to bottom, every example must work. This is a
documentation change only: do not change the code or the tests.
