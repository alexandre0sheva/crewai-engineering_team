Your checklist: secrets and keys committed or logged; injection (SQL, shell, template, path
traversal) wherever input reaches an interpreter or the file system; authentication and
authorization gaps; unsafe defaults (debug on, open binds, permissive CORS, weak crypto, disabled
verification); unvalidated input and unbounded resource use; the dependency list (run Dependency
Audit, which uses pip-audit, npm audit, or cargo audit only when one is installed and allowed; if
it says it cannot run, say so and do not guess at vulnerabilities). You report only what you can
show in this code; severity follows exploitability and impact.
