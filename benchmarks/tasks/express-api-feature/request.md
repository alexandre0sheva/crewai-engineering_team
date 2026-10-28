# Add loans to the library API

Members borrow books. Please add loans to the API:

- Every book gains a field `onLoanTo`: the member who has it, or `null`. It appears wherever a book
  is returned (`GET /books`, `GET /books/:id`, `POST /books`, and the new endpoints).
- `POST /books/:id/loan` with JSON `{"member": "ana"}` lends the book to that member and answers 200
  with the updated book. An unknown book is 404; a missing or empty `member` is 400 with
  `{"error": "member must be a non-empty string"}`; a book already on loan is 409 with
  `{"error": "book 1 is already on loan"}` (with the book's id).
- `POST /books/:id/return` takes the book back and answers 200 with the updated book. An unknown
  book is 404; a book that is not on loan is 409 with `{"error": "book 1 is not on loan"}`.
- `GET /books?available=true` lists only the books not on loan, `?available=false` only those on
  loan (both ordered by id, like `GET /books`); any other value is 400.

Errors stay JSON `{"error": "..."}`. Everything that exists today keeps working as it does (apart
from the new field), including validation messages and status codes. `createApp(store)` in
`src/app.ts` must still build the app from a `BookStore`, and `new BookStore([{title, author}, ...])`
must still create a store with those books (ids from 1). Add tests for the new endpoints to the
project's suite (`npm test`).
