import { type CartLine } from "./cart.ts";
import { OrderSummary } from "./OrderSummary.tsx";

/** The cart page: the lines in the order they were added, then the summary sorted by name. */
export function CartPage({ lines }: { lines: CartLine[] }) {
  return (
    <main>
      <h1>Your cart</h1>
      <ol className="cart-lines">
        {lines.map((line) => (
          <li key={line.sku} data-sku={line.sku}>
            {line.name}
          </li>
        ))}
      </ol>
      <OrderSummary lines={lines} sortByName />
    </main>
  );
}
