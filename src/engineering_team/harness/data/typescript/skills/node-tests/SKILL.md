---
name: node-tests
description: Vitest or Jest tests.
teammates: [backend_engineer, frontend_engineer, quality_engineer, generalist_engineer, debugger]
---
# Node tests (Vitest, Jest)

- Find the runner in `package.json` (`scripts.test`, devDependencies) and use the project's script:
  `npm test -- path/to/file.test.ts` (or `pnpm`/`yarn`, following the lockfile).
- One test: Vitest `npx vitest run file -t "name"`, Jest `npx jest file -t "name"`. Never leave
  `.only` or `.skip` behind.
- Async tests must `await` (or return) every promise; a missing `await` passes a test that should
  fail. Fake timers (`vi.useFakeTimers()`, `jest.useFakeTimers()`) must be restored.
- Module mocks (`vi.mock`, `jest.mock`) are hoisted; reset them between tests (`restoreMocks` or
  `vi.restoreAllMocks()`), or state leaks from one test to the next.
- ESM versus CommonJS errors (`Cannot use import statement outside a module`, `require is not
  defined`) come from `"type"` in `package.json` and the runner's transform, not from the test.
- TypeScript errors in tests: run `npx tsc --noEmit`; the runner may strip types without checking.
