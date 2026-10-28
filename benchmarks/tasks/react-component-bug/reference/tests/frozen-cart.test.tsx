import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { type CartLine } from "../src/cart.ts";
import { CartPage } from "../src/CartPage.tsx";
import { OrderSummary } from "../src/OrderSummary.tsx";

function frozenCart(): CartLine[] {
  return Object.freeze([
    Object.freeze({ sku: "t-1", name: "Teapot", unitCents: 2450, quantity: 1 }),
    Object.freeze({ sku: "c-2", name: "Cups", unitCents: 600, quantity: 4 }),
  ]) as CartLine[];
}

it("renders frozen lines sorted by name without touching them", () => {
  const lines = frozenCart();
  const html = renderToStaticMarkup(<OrderSummary lines={lines} sortByName />);
  expect(html.indexOf("Cups")).toBeLessThan(html.indexOf("Teapot"));
  expect(lines.map((line) => line.sku)).toEqual(["t-1", "c-2"]);
});

it("keeps the cart page in the order the lines were added", () => {
  const lines = [...frozenCart()];
  renderToStaticMarkup(<CartPage lines={lines} />);
  const html = renderToStaticMarkup(<CartPage lines={lines} />);
  expect(lines.map((line) => line.sku)).toEqual(["t-1", "c-2"]);
  expect(html.indexOf('data-sku="t-1"')).toBeLessThan(html.indexOf('data-sku="c-2"'));
});
