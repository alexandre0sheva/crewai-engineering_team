import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { type CartLine } from "../src/cart.ts";
import { OrderSummary } from "../src/OrderSummary.tsx";

const LINES: CartLine[] = [
  { sku: "t-1", name: "Teapot", unitCents: 2450, quantity: 1 },
  { sku: "c-2", name: "Cups", unitCents: 600, quantity: 4 },
];

describe("OrderSummary", () => {
  it("shows each line and the totals", () => {
    const html = renderToStaticMarkup(<OrderSummary lines={LINES} />);
    expect(html).toContain("1 × Teapot: $24.50");
    expect(html).toContain("4 × Cups: $24.00");
    expect(html).toContain('data-testid="subtotal">$48.50<');
    expect(html).toContain('data-testid="shipping">$4.95<');
    expect(html).toContain('data-testid="total">$53.45<');
  });

  it("ships for free from $50 and says when the cart is empty", () => {
    const big = [...LINES, { sku: "s-3", name: "Strainer", unitCents: 150, quantity: 1 }];
    expect(renderToStaticMarkup(<OrderSummary lines={big} />)).toContain(">Free<");
    expect(renderToStaticMarkup(<OrderSummary lines={[]} />)).toContain("Your cart is empty.");
  });
});
