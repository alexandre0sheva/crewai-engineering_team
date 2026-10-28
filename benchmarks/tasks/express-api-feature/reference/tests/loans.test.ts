import assert from "node:assert/strict";
import { test } from "node:test";
import { send, serve } from "./helpers.ts";

const BOOKS = [
  { title: "Kindred", author: "Octavia E. Butler" },
  { title: "Dune", author: "Frank Herbert" },
];

test("a book is lent, refused while on loan, and returned", async () => {
  const api = await serve(BOOKS);
  try {
    const lent = await send(`${api.url}/books/1/loan`, "POST", { member: "ana" });
    assert.equal(lent.status, 200);
    assert.equal(lent.body.onLoanTo, "ana");
    const again = await send(`${api.url}/books/1/loan`, "POST", { member: "bo" });
    assert.deepEqual(again, { status: 409, body: { error: "book 1 is already on loan" } });
    const back = await send(`${api.url}/books/1/return`, "POST");
    assert.equal(back.body.onLoanTo, null);
    const twice = await send(`${api.url}/books/1/return`, "POST");
    assert.deepEqual(twice, { status: 409, body: { error: "book 1 is not on loan" } });
  } finally {
    api.stop();
  }
});

test("loans validate the member and the book", async () => {
  const api = await serve(BOOKS);
  try {
    assert.equal((await send(`${api.url}/books/1/loan`, "POST", {})).status, 400);
    assert.equal((await send(`${api.url}/books/7/loan`, "POST", { member: "ana" })).status, 404);
    assert.equal((await send(`${api.url}/books/7/return`, "POST")).status, 404);
  } finally {
    api.stop();
  }
});

test("books can be filtered by availability", async () => {
  const api = await serve(BOOKS);
  try {
    await send(`${api.url}/books/2/loan`, "POST", { member: "ana" });
    const free = await send(`${api.url}/books?available=true`, "GET");
    assert.deepEqual(free.body.map((b: { id: number }) => b.id), [1]);
    const out = await send(`${api.url}/books?available=false`, "GET");
    assert.deepEqual(out.body.map((b: { id: number }) => b.id), [2]);
    assert.equal((await send(`${api.url}/books?available=maybe`, "GET")).status, 400);
  } finally {
    api.stop();
  }
});
