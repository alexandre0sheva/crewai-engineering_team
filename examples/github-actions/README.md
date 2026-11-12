# GitHub Actions examples

Two workflows for repositories that use the team in CI. Neither is a runnable scenario like the other
examples: copy them into `.github/workflows/` of your repository, add the provider key as a **repository
secret**, and read [Usage in CI](../../docs/USAGE.md#in-ci-github-actions) and
[Safety in CI](../../docs/SAFETY.md#github-actions) first (cost, the permissions each needs, what fork pull
requests get).

| File | Trigger | What it does |
|------|---------|--------------|
| [`review.yml`](review.yml) | a pull request is opened or updated | `uses:` the root [`action.yml`](../../action.yml): a read-only review of the diff; SARIF to code scanning, one summary comment, job fails at `fail-on` |
| [`fix-on-label.yml`](fix-on-label.yml) | an issue gets the label `engineering-team:fix` | `do --mode fix --issue ... --open-pr` in the Docker sandbox: a **draft** pull request when the checks pass |

This repository checks the action on its own pull requests with
[`.github/workflows/review.yml`](../../.github/workflows/review.yml), in `--fake` mode (a scripted model
that finds nothing: no key, no spend).
