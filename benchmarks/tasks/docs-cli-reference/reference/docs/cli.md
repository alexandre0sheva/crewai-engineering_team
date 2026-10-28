# tasktool command-line reference

```text
python -m tasktool [--file FILE] COMMAND ...
```

Global option (before the command):

- `--file FILE`: where the tasks are kept. Default: `tasks.json` in the current directory.

## add

Add a task.

```bash
python -m tasktool add "Renew passport" --priority high --due 2026-05-01 --tag admin --tag travel
```

- `TITLE` (required): what the task is.
- `--priority low|normal|high`: default `normal`.
- `--due YYYY-MM-DD`: the due date. Default: none.
- `--tag TAG`: a tag; repeat it for several tags. Default: no tags. Duplicates are dropped.

Prints `added ID: TITLE`. Ids start at 1.

## list

List the tasks that are not finished.

```bash
python -m tasktool list --sort due
python -m tasktool list --all --tag admin
```

- `--all`: include finished tasks. Off by default.
- `--tag TAG`: only tasks with this tag. Default: every task.
- `--sort id|due|priority`: the order. Default: `id`. `due` puts tasks without a due date last;
  `priority` lists high, then normal, then low.

Each line is `[x] ID TITLE [PRIORITY] (due DATE)`, with `x` for a finished task.

## done

Mark one or more tasks finished.

```bash
python -m tasktool done 1
```

- `IDS` (required, one or more): the task ids. An unknown id prints `error: no task ID`, exits
  with status 2, and changes nothing.

## export

Write every task as CSV, with the header `id,title,priority,due,tags,done`.

```bash
python -m tasktool export --output tasks.csv --delimiter ";"
```

- `--output FILE`: the file to write, or `-` for standard output. Default: `-`.
- `--delimiter CHAR`: the field separator. Default: `,`.
