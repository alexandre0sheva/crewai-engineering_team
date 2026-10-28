# library-api

A small HTTP API for a lending library, on Express 5. Data is kept in memory.

```bash
npm install
PORT=3000 npm start
npm test
```

| Method and path | What it does |
|-----------------|--------------|
| `GET /books` | every book, by id |
| `GET /books/:id` | one book, or 404 |
| `POST /books` | add a book: JSON `{"title": "...", "author": "..."}`; 201 with the book, 400 on bad input |
| `GET /health` | `{"status": "ok"}` |

A book is `{"id": 1, "title": "...", "author": "..."}`. Errors are JSON `{"error": "..."}`.
`createApp(store)` in `src/app.ts` builds the application (the tests use it with a fresh store).
