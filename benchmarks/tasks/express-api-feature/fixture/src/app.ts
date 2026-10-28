import express, { type NextFunction, type Request, type Response } from "express";
import { type BookStore } from "./store.ts";
import { readId, readNewBook } from "./validate.ts";

export function createApp(store: BookStore): express.Express {
  const app = express();
  app.use(express.json());

  app.get("/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.get("/books", (_req, res) => {
    res.json(store.all());
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
