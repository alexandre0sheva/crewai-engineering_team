# Inventory service

A small JSON API over WSGI (`inventory.app.application`) keeping items in memory.

| Method | Path | Meaning |
|--------|------|---------|
| GET | `/items` | all items, by id |
| POST | `/items` | create `{"name", "qty", "price"}` |
| GET | `/items/<id>` | one item |
| DELETE | `/items/<id>` | remove it |

Tests: `python -m unittest discover -s tests`.
