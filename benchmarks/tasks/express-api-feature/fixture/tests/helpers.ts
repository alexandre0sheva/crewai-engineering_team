import type { AddressInfo } from "node:net";
import { createApp } from "../src/app.ts";
import { BookStore, type NewBook } from "../src/store.ts";

/** Start the app on a free port with these books; returns its base URL and a stop function. */
export async function serve(books: NewBook[] = []): Promise<{ url: string; stop: () => void }> {
  const server = createApp(new BookStore(books)).listen(0);
  await new Promise((resolve) => server.once("listening", resolve));
  const { port } = server.address() as AddressInfo;
  return { url: `http://127.0.0.1:${port}`, stop: () => server.close() };
}

export async function send(url: string, method: string, body?: unknown) {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}
