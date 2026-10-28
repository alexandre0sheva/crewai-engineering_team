export interface CartLine {
  sku: string;
  name: string;
  unitCents: number;
  quantity: number;
}

export const FREE_SHIPPING_FROM_CENTS = 5000;
export const SHIPPING_CENTS = 495;

export function lineTotal(line: CartLine): number {
  return line.unitCents * line.quantity;
}

export function subtotal(lines: readonly CartLine[]): number {
  return lines.reduce((sum, line) => sum + lineTotal(line), 0);
}

export function shipping(lines: readonly CartLine[]): number {
  if (lines.length === 0) return 0;
  return subtotal(lines) >= FREE_SHIPPING_FROM_CENTS ? 0 : SHIPPING_CENTS;
}
