export interface Summary {
  count: number;
  total: number;
  fastest: number;
  slowest: number;
  average: number;
}

/** Count, total, fastest, slowest and average of some durations in seconds (at least one). */
export function summarise(durations: readonly number[]): Summary {
  if (durations.length === 0) {
    throw new RangeError("nothing to summarise");
  }
  const total = durations.reduce((sum, value) => sum + value, 0);
  return {
    count: durations.length,
    total,
    fastest: Math.min(...durations),
    slowest: Math.max(...durations),
    average: total / durations.length,
  };
}
