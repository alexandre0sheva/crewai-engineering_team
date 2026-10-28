import assert from "node:assert/strict";
import { test } from "node:test";
import { main } from "../src/cli.ts";
import { formatDuration } from "../src/format.ts";

test("rounding up carries into the next unit instead of printing 60", () => {
  assert.equal(formatDuration(59.5), "1m");
  assert.equal(formatDuration(3_599.5), "1h");
  assert.equal(formatDuration(86_399.6), "1d");
  assert.deepEqual(main(["total", "1.5m", "58m", "29.5s"]), ["1h"]);
});
