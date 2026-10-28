# notes

A small multi-user notes service (Python standard library only): `notes/storage.py` (SQLite),
`notes/service.py` (what a user may do: only ever their own notes), `notes/web.py` (a JSON WSGI
app behind an auth proxy that sets `X-User`).

```bash
python -m unittest discover -s tests
```
