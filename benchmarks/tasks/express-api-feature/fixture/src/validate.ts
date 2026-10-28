import type { NewBook } from "./store.ts";

/** The problem with a request body for a new book, or the book. */
export function readNewBook(body: unknown): { book: NewBook } | { problem: string } {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return { problem: "the body must be a JSON object" };
  }
  const { title, author } = body as Record<string, unknown>;
  for (const [name, value] of [["title", title], ["author", author]] as const) {
    if (typeof value !== "string" || value.trim() === "") {
      return { problem: `${name} must be a non-empty string` };
    }
  }
  return { book: { title: (title as string).trim(), author: (author as string).trim() } };
}

/** A path id: a positive whole number, or undefined. */
export function readId(raw: string): number | undefined {
  return /^[1-9]\d*$/.test(raw) ? Number(raw) : undefined;
}
