import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { send, serve } from "./helpers.ts";

let api: { url: string; stop: () => void };
before(async () => {
  api = await serve([{ title: "Kindred", author: "Octavia E. Butler" }]);
});
after(() => api.stop());

test("lists and fetches books", async () => {
  assert.deepEqual((await send(`${api.url}/books`, "GET")).body, [
    { id: 1, title: "Kindred", author: "Octavia E. Butler" },
  ]);
  assert.equal((await send(`${api.url}/books/1`, "GET")).status, 200);
  assert.equal((await send(`${api.url}/books/9`, "GET")).status, 404);
});

test("adds a book and validates the body", async () => {
  const created = await send(`${api.url}/books`, "POST", { title: " Dune ", author: "F. Herbert" });
  assert.equal(created.status, 201);
  assert.deepEqual(created.body, { id: 2, title: "Dune", author: "F. Herbert" });
  const bad = await send(`${api.url}/books`, "POST", { title: "" });
  assert.equal(bad.status, 400);
  assert.equal(bad.body.error, "title must be a non-empty string");
});
