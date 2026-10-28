import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDuration } from "../src/format.ts";

test("prints the largest unit first and leaves out zero parts", () => {
  assert.equal(formatDuration(3_725), "1h2m5s");
  assert.equal(formatDuration(90_000), "1d1h");
  assert.equal(formatDuration(0), "0s");
});

test("refuses negative and non-finite values", () => {
  assert.throws(() => formatDuration(-1), RangeError);
  assert.throws(() => formatDuration(Number.NaN), RangeError);
});
