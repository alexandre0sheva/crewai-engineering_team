import assert from "node:assert/strict";
import { test } from "node:test";
import { DurationError } from "../src/errors.ts";
import { parseDuration } from "../src/parse.ts";

test("parses every unit and combinations", () => {
  assert.equal(parseDuration("45s"), 45);
  assert.equal(parseDuration("1h30m"), 5_400);
  assert.equal(parseDuration("2d4h"), 187_200);
  assert.equal(parseDuration(" 1.5H "), 5_400);
});

test("rejects what it cannot read", () => {
  assert.throws(() => parseDuration(""), DurationError);
  assert.throws(() => parseDuration("10x"), /unknown unit "x"/);
  assert.throws(() => parseDuration("1h 30m"), /cannot read/);
});
