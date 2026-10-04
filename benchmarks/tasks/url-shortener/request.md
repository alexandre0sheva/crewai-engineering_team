# URL shortener

Build a small URL-shortening HTTP service in Python 3.11 or newer, using only the standard
library (`http.server`, `sqlite3`, ... no third-party packages).

Start it from the project root with `python -m shortener --port PORT [--db PATH]` (a top-level
package `shortener/` with a `__main__.py` in the project root, not under `src/`). It listens on
`127.0.0.1:PORT`. Without `--db` it keeps everything in memory; with `--db PATH` the links are
stored in a SQLite file at PATH, so they survive a restart.

## API

All responses except redirects are JSON (`Content-Type: application/json`).

- `POST /shorten` with a JSON body `{"url": "https://example.com/some/page"}`:
  - the first time a URL is shortened: status 201 and
    `{"code": CODE, "url": URL, "short_url": "http://127.0.0.1:PORT/CODE"}`, where CODE is 6
    characters from `A-Za-z0-9`;
  - the same URL again: status 200 and the same body (same code);
  - a body that is not JSON, not an object, has no string `url`, or whose url is not an absolute
    `http://` or `https://` URL with a host: status 400 and `{"error": "..."}`.
- `GET /CODE`: status 302 with a `Location` header holding the original URL, and the link's hit
  counter goes up by one. An unknown code: status 404 and `{"error": "..."}`.
- `GET /stats/CODE`: status 200 and `{"code": CODE, "url": URL, "hits": N}` (N is how many
  redirects were served for that code); an unknown code: 404 and `{"error": "..."}`.

Any other path is a 404 JSON error. Add tests and a short README.
