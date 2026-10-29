# Triage, openai/gpt-6-luna

- Cases: 74
- Primary intent: 97.3 %
- Size: 78.4 %
- Target (new/existing): 100.0 %
- Secondary intents found: 88.9 %
- Asked when expected / not when not: 100.0 %
- Signals-only fallbacks: 0

## Confusion matrix (rows: expected primary intent, columns: predicted)

| expected | bugfix | devops | docs | feature | new_project | performance | plan_only | question | refactor | research | review | security_audit | tests | upgrade_deps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bugfix | 9 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| devops |  | 4 |  |  |  |  |  |  |  |  |  |  |  |  |
| docs |  |  | 3 |  |  |  |  |  |  |  |  |  |  |  |
| feature |  |  |  | 7 |  |  |  |  | 1 |  |  |  |  |  |
| new_project |  |  |  |  | 7 |  |  |  |  |  |  |  |  |  |
| performance |  |  |  |  |  | 4 |  |  |  |  |  |  |  |  |
| plan_only |  |  |  |  |  |  | 4 |  |  |  |  |  |  |  |
| question |  |  |  |  | 1 |  |  | 6 |  |  |  |  |  |  |
| refactor |  |  |  |  |  |  |  |  | 5 |  |  |  |  |  |
| research |  |  |  |  |  |  |  |  |  | 4 |  |  |  |  |
| review |  |  |  |  |  |  |  |  |  |  | 6 |  |  |  |
| security_audit |  |  |  |  |  |  |  |  |  |  |  | 4 |  |  |
| tests |  |  |  |  |  |  |  |  |  |  |  |  | 4 |  |
| upgrade_deps |  |  |  |  |  |  |  |  |  |  |  |  |  | 5 |

## Misses

| case | expected | predicted | size |
|---|---|---|---|
| question-why-trace | question, bugfix | question | small → trivial |
| question-no-repo-flask-trace | question | new_project | trivial → small |
| research-ci-service | research | research | small → trivial |
| review-pasted-diff | review | review | trivial → small |
| bugfix-ledger-misleading-trace | bugfix | bugfix | medium → small |
| feature-vague-better | feature | refactor | small → medium |
| new-empty-dir-markdown | new_project | new_project | small → medium |
| new-ambiguous-contacts | new_project | new_project | small → medium |
| audit-ledger-cves | security_audit | security_audit | small → medium |
| perf-node-memory | performance | performance | small → medium |
| devops-kubernetes-cronjob | devops | devops | small → medium |
| plan-sqlite-migration | plan_only | plan_only | small → medium |
| plan-design-doc | plan_only | plan_only | small → medium |
| plan-break-down-accounts | plan_only | plan_only, feature, security_audit | small → large |
| plan-roadmap-split | plan_only | plan_only | small → medium |
| new-empty-git-question | question | question | trivial → small |

# Triage, signals only (no model)

- Cases: 74
- Primary intent: 83.8 %
- Size: 73.0 %
- Target (new/existing): 100.0 %
- Secondary intents found: 66.7 %
- Asked when expected / not when not: 0.0 %
- Signals-only fallbacks: 74

## Confusion matrix (rows: expected primary intent, columns: predicted)

| expected | bugfix | devops | docs | feature | new_project | performance | plan_only | question | refactor | research | review | security_audit | tests | upgrade_deps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bugfix | 7 |  | 1 |  |  |  |  |  |  |  |  |  | 1 |  |
| devops |  | 4 |  |  |  |  |  |  |  |  |  |  |  |  |
| docs |  |  | 2 |  |  |  |  |  |  |  |  |  |  | 1 |
| feature |  |  | 1 | 6 |  |  |  |  |  |  |  |  | 1 |  |
| new_project |  |  | 2 |  | 5 |  |  |  |  |  |  |  |  |  |
| performance |  |  |  |  |  | 4 |  |  |  |  |  |  |  |  |
| plan_only |  |  |  |  |  |  | 4 |  |  |  |  |  |  |  |
| question |  |  |  |  |  |  |  | 7 |  |  |  |  |  |  |
| refactor |  |  |  |  |  |  |  |  | 5 |  |  |  |  |  |
| research |  |  |  | 1 |  |  | 1 | 1 |  | 1 |  |  |  |  |
| review |  |  |  |  |  |  |  |  |  |  | 6 |  |  |  |
| security_audit |  |  |  |  |  |  |  | 1 |  |  |  | 3 |  |  |
| tests |  |  |  | 1 |  |  |  |  |  |  |  |  | 3 |  |
| upgrade_deps |  |  |  |  |  |  |  |  |  |  |  |  |  | 5 |

## Misses

| case | expected | predicted | size |
|---|---|---|---|
| question-why-trace | question, bugfix | question | small → trivial |
| research-orm-compare | research | research | small → trivial |
| research-express-validation | research | plan_only | small → trivial |
| research-ci-service | research | question, devops | small → trivial |
| research-rate-limiting | research | feature | small → small |
| review-feature-branch | review | review | small → trivial |
| review-pr-sessions | review | review | small → trivial |
| review-against-main | review | review | small → trivial |
| review-uncommitted-files | review | review | small → trivial |
| bugfix-ledger-misleading-trace | bugfix | bugfix | medium → small |
| feature-loans-endpoint | feature, tests | tests | medium → small |
| feature-multi-carrier | feature | feature | medium → small |
| new-url-shortener | new_project | docs | medium → small |
| new-booking-platform | new_project, devops | docs, devops | large → medium |
| refactor-extract-pricing | refactor | refactor | medium → small |
| refactor-ledger-plugins | refactor, docs | refactor, docs | large → small |
| tests-validation-errors | tests | feature | small → small |
| docs-readme-outdated | docs | upgrade_deps, docs | small → small |
| docs-with-fix | bugfix, docs | docs | small → small |
| audit-password-hashing | security_audit | question | small → trivial |
| plan-sqlite-migration | plan_only | plan_only | small → trivial |
| plan-design-doc | plan_only | plan_only | small → trivial |
| plan-break-down-accounts | plan_only | plan_only | small → trivial |
| plan-roadmap-split | plan_only | plan_only | small → trivial |
| mixed-bugfix-tests | bugfix, tests | tests | small → small |
| mixed-feature-docs | feature, docs | docs | small → small |
