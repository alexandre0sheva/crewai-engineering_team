# Notes CLI

    python -m notes add "buy milk" --tag home
    python -m notes list [--tag home]
    python -m notes search milk
    python -m notes delete 1

Notes are stored in the JSON file named by `NOTES_FILE` (default `notes.json`).
Run the tests with `python -m unittest discover -s tests`.
