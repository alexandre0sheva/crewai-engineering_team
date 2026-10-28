import assert from "node:assert/strict";
import { test } from "node:test";
import { main } from "../src/cli.ts";

test("total adds durations", () => {
  assert.deepEqual(main(["total", "1h30m", "45m"]), ["2h15m"]);
});

test("a usage error names both commands", () => {
  assert.throws(() => main(["nope"]), /durations total/);
});
