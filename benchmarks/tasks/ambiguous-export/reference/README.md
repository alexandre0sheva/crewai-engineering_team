# contacts

Sorts the club's contact list (a CSV file with a `name` column) for the printed phone list.

```bash
python -m contacts sort members.csv phone-list.csv
python -m unittest discover -s tests
```

## Assumption: "sorted by name" means by last name

The request says "sorted by name" and the names are full names ("Grace Hopper"), so it could mean
by first name or by last name. A phone list is usually ordered by last name, so the tool sorts by
the last word of the name (case-insensitively), then by the whole name for people who share a last
name. If the club wants first names, `contacts/sorting.py` is the one place to change.

The other columns are kept as they are, in their original order. A missing input file or a file
without a `name` column is an error (exit status 2).
