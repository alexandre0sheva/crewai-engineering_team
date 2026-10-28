import { UNITS } from "./units.ts";

/** `3725` -> `1h2m5s`: rounded to the nearest second, largest unit first, zero parts left out. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    throw new RangeError(`cannot format ${seconds} seconds`);
  }
  const parts: string[] = [];
  let rest = seconds;
  for (const [unit, size] of UNITS) {
    const amount = size === 1 ? Math.round(rest) : Math.floor(rest / size);
    if (amount > 0) {
      parts.push(`${amount}${unit}`);
    }
    rest -= amount * size;
  }
  return parts.length > 0 ? parts.join("") : "0s";
}
