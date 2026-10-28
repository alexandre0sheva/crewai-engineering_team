/** Cents as a price label: 1999 -> "$19.99". */
export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const whole = Math.abs(cents);
  return `${sign}$${Math.floor(whole / 100)}.${String(whole % 100).padStart(2, "0")}`;
}
