# The cart page crashes since the cart state became immutable

We moved the cart into a store that freezes its state (`Object.freeze` on the array of lines and
on each line). Since then the cart page crashes with the error in the attached trace, inside
`OrderSummary`. Before the move there was a quieter symptom of the same thing: after the cart page
had rendered once, the "Your cart" list showed the lines sorted by name instead of in the order
they were added, because the cart array itself had been reordered.

Expected:

- `OrderSummary` never changes the `lines` it is given (the array or the lines in it), so it
  works with frozen lines and leaves the caller's order alone.
- With `sortByName`, the summary rows are still ordered by name; without it, in the given order.
- `CartPage` lists the lines in the order they were added, every time it renders.
- Totals, shipping, the empty-cart message and the markup stay exactly as they are.

Please fix it and add a regression test to the Vitest suite (`npm test`).
