# Todo web app

Build a minimal todo list web app in Python 3.11 or newer, using only the standard library
(`http.server`; no frameworks, no third-party packages, no JavaScript needed).

Start it from the project root with `python app.py --port PORT` (a single `app.py` is fine; you
may add modules next to it). It listens on `127.0.0.1:PORT` and keeps its todos in memory.

## Pages and forms

- `GET /` returns an HTML page (`text/html`) that lists every todo and has a form
  `<form method="post" action="/add">` with a text input named `text`.
- Each todo is rendered as `<li data-id="ID">TEXT</li>`, in creation order, where ID is its
  integer id (starting at 1) and TEXT is its text, HTML-escaped. A todo that is done has the
  class `done`: `<li data-id="ID" class="done">`. Each todo also has a small form to toggle it
  (`POST /toggle/ID`) and one to delete it (`POST /delete/ID`).
- `POST /add` (form field `text`) adds a todo and answers `303` with `Location: /`. Empty or
  whitespace-only text adds nothing (still `303`).
- `POST /toggle/ID` flips the todo between not done and done, and answers `303` to `/`;
  `POST /delete/ID` removes it, also `303`. An unknown ID is a `404`.

## JSON API

- `GET /api/todos` returns a JSON array of `{"id": 1, "text": "...", "done": false}`.
- `POST /api/todos` with a JSON body `{"text": "..."}` creates a todo and returns `201` with the
  new object. Missing, non-string, or blank text is a `400` with `{"error": "..."}`.

Both ways of adding and changing todos work on the same list. Add tests and a short README.
