import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CartPage } from "../src/CartPage.tsx";
import { OrderSummary } from "../src/OrderSummary.tsx";

type Line = { sku: string; name: string; unitCents: number; quantity: number };
const LINES: Line[] = [
  { sku: "t-1", name: "Teapot", unitCents: 2450, quantity: 1 },
  { sku: "c-2", name: "Cups", unitCents: 600, quantity: 4 },
  { sku: "k-3", name: "Kettle", unitCents: 3125, quantity: 2 },
];
const frozen = () => Object.freeze(LINES.map((line) => Object.freeze({ ...line }))) as Line[];
const order = (html: string, attribute = "data-sku") =>
  [...html.matchAll(new RegExp(`${attribute}="([^"]+)"`, "g"))].map((match) => match[1]);

describe("frozen-lines", () => {
  it("renders frozen lines, sorted by name in the summary", () => {
    const html = renderToStaticMarkup(<OrderSummary lines={frozen()} sortByName />);
    expect(order(html)).toEqual(["c-2", "k-3", "t-1"]);
    const page = renderToStaticMarkup(<CartPage lines={frozen()} />);
    expect(page).toContain('data-testid="total">$111.00<');
  });
});

describe("props-untouched", () => {
  it("leaves the caller's array and lines alone", () => {
    const lines = LINES.map((line) => ({ ...line }));
    const before = JSON.stringify(lines);
    renderToStaticMarkup(<OrderSummary lines={lines} sortByName />);
    expect(JSON.stringify(lines)).toBe(before);
  });
  it("keeps the cart page in the added order on every render", () => {
    const lines = LINES.map((line) => ({ ...line }));
    for (let i = 0; i < 2; i++) {
      const html = renderToStaticMarkup(<CartPage lines={lines} />);
      const list = html.slice(html.indexOf('<ol class="cart-lines">'), html.indexOf("</ol>"));
      expect(order(list)).toEqual(["t-1", "c-2", "k-3"]);
    }
    expect(lines.map((line) => line.sku)).toEqual(["t-1", "c-2", "k-3"]);
  });
});

describe("summary-unchanged", () => {
  it("keeps rows, totals and shipping", () => {
    const html = renderToStaticMarkup(<OrderSummary lines={LINES.slice(0, 2)} />);
    expect(order(html)).toEqual(["t-1", "c-2"]);
    expect(html).toContain("1 × Teapot: $24.50");
    expect(html).toContain('data-testid="subtotal">$48.50<');
    expect(html).toContain('data-testid="shipping">$4.95<');
    expect(html).toContain('data-testid="total">$53.45<');
    const free = renderToStaticMarkup(<OrderSummary lines={LINES} />);
    expect(free).toContain('data-testid="shipping">Free<');
    const empty = renderToStaticMarkup(<OrderSummary lines={[]} sortByName />);
    expect(empty).toContain('<p class="empty">Your cart is empty.</p>');
    expect(empty).toContain('data-testid="total">$0.00<');
  });
});
