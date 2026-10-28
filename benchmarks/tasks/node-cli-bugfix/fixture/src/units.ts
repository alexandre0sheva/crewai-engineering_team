/** Seconds per unit, largest first (the order formatDuration prints them in). */
export const UNITS: ReadonlyArray<readonly [string, number]> = [
  ["d", 86_400],
  ["h", 3_600],
  ["m", 60],
  ["s", 1],
];

export const SECONDS_PER_UNIT: ReadonlyMap<string, number> = new Map(UNITS);
