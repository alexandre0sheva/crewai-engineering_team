---
name: react-testing
description: Testing React components and UI states.
teammates: [frontend_engineer, quality_engineer, generalist_engineer]
---
# Testing React components

- Use Testing Library: render, then query the way a user finds things - `getByRole` (with
  `name`), `getByLabelText`, `getByText`; `getByTestId` only as a last resort.
- Interact with `@testing-library/user-event` (`await user.click(...)`, `await user.type(...)`),
  not by calling handlers.
- Asynchronous UI: `await screen.findByText(...)` or `waitFor(...)`; never fixed sleeps.
- Cover the states the request names: loading, empty, success and error, each with its own test.
- Mock the network at its boundary (`fetch`, an API module, or MSW), not React internals.
- `act(...)` warnings mean an update happened after the test stopped waiting: await it.
