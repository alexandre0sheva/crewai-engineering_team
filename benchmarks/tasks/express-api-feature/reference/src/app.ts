import express, { type NextFunction, type Request, type Response } from "express";
import { type BookStore } from "./store.ts";
import { readId, readMember, readNewBook } from "./validate.ts";

export function createApp(store: BookStore): express.Express {
  const app = express();
  app.use(express.json());

  app.get("/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.get("/books", (req, res) => {
    const available = req.query.available;
    if (available === undefined) {
      res.json(store.all());
      return;
    }
    if (available !== "true" && available !== "false") {
      res.status(400).json({ error: "available must be true or false" });
      return;
    }
    const wanted = available === "true";
    res.json(store.all().filter((book) => (book.onLoanTo === null) === wanted));
  });

  app.get("/books/:id", (req, res) => {
    const id = readId(req.params.id);
    const book = id === undefined ? undefined : store.get(id);
    if (!book) {
      res.status(404).json({ error: `no book ${req.params.id}` });
      return;
    }
    res.json(book);
  });

  app.post("/books", (req, res) => {
    const read = readNewBook(req.body);
    if ("problem" in read) {
      res.status(400).json({ error: read.problem });
      return;
    }
    res.status(201).json(store.add(read.book));
  });

  app.post("/books/:id/loan", (req, res) => {
    const id = readId(req.params.id);
    if (id === undefined || !store.get(id)) {
      res.status(404).json({ error: `no book ${req.params.id}` });
      return;
    }
    const read = readMember(req.body);
    if ("problem" in read) {
      res.status(400).json({ error: read.problem });
      return;
    }
    const result = store.lend(id, read.member);
    if (result === "on-loan" || result === "missing") {
      res.status(409).json({ error: `book ${id} is already on loan` });
      return;
    }
    res.json(result);
  });

  app.post("/books/:id/return", (req, res) => {
    const id = readId(req.params.id);
    const result = id === undefined ? "missing" : store.giveBack(id);
    if (result === "missing") {
      res.status(404).json({ error: `no book ${req.params.id}` });
      return;
    }
    if (result === "not-on-loan") {
      res.status(409).json({ error: `book ${id} is not on loan` });
      return;
    }
    res.json(result);
  });

  app.use((_req, res) => {
    res.status(404).json({ error: "not found" });
  });

  // Malformed JSON and other errors: JSON, never an HTML page.
  app.use((error: { status?: number }, _req: Request, res: Response, _next: NextFunction) => {
    const status = error.status === 400 ? 400 : 500;
    res.status(status).json({ error: status === 400 ? "malformed JSON" : "internal error" });
  });
  return app;
}
