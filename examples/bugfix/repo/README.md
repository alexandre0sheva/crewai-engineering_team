# shop

Prints a summary of a shopping cart stored as JSON.

    python -m shop report --file cart.json

`cart.json` is a list of `{"name": ..., "price": ..., "qty": ...}`. Tests:
`python -m unittest discover -s tests`.
