import { DurationError } from "./errors.ts";
import { SECONDS_PER_UNIT } from "./units.ts";

const PART = /(\d+(?:\.\d+)?)([a-z]+)/g;

/** Seconds in a duration such as `1h30m` or `1.5h` (not rounded). */
export function parseDuration(text: string): number {
  const value = text.trim().toLowerCase();
  if (value === "") {
    throw new DurationError("empty duration");
  }
  let total = 0;
  let consumed = 0;
  for (const match of value.matchAll(PART)) {
    const [whole, amount, unit] = match;
    const size = SECONDS_PER_UNIT.get(unit);
    if (size === undefined) {
      throw new DurationError(`unknown unit "${unit}" in "${text}"`);
    }
    if (match.index !== consumed) {
      break;
    }
    total += Number(amount) * size;
    consumed += whole.length;
  }
  if (consumed !== value.length) {
    throw new DurationError(`cannot read "${text}"`);
  }
  return total;
}
