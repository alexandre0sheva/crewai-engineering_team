import { type CartLine, lineTotal, shipping, subtotal } from "./cart.ts";
import { formatCents } from "./money.ts";

export interface OrderSummaryProps {
  lines: CartLine[];
  /** Order the lines by name (the summary page) instead of as they were added. */
  sortByName?: boolean;
}

/** The order summary box: one row per line, then subtotal, shipping and total. */
export function OrderSummary({ lines, sortByName = false }: OrderSummaryProps) {
  // Sort a copy: the lines are the caller's (often frozen store state) and must not change.
  const shown = sortByName ? [...lines].sort((a, b) => a.name.localeCompare(b.name)) : lines;
  const sub = subtotal(lines);
  const ship = shipping(lines);
  return (
    <section className="order-summary" aria-label="Order summary">
      {shown.length === 0 ? (
        <p className="empty">Your cart is empty.</p>
      ) : (
        <ul>
          {shown.map((line) => (
            <li key={line.sku} data-sku={line.sku}>
              {line.quantity} × {line.name}: {formatCents(lineTotal(line))}
            </li>
          ))}
        </ul>
      )}
      <dl>
        <dt>Subtotal</dt>
        <dd data-testid="subtotal">{formatCents(sub)}</dd>
        <dt>Shipping</dt>
        <dd data-testid="shipping">{ship === 0 ? "Free" : formatCents(ship)}</dd>
        <dt>Total</dt>
        <dd data-testid="total">{formatCents(sub + ship)}</dd>
      </dl>
    </section>
  );
}
