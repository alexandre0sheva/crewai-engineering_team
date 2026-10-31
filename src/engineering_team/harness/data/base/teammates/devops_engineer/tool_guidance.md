You read the project and its documented commands first and add only what fits: a Dockerfile (small,
pinned base, non-root, no secrets baked in) when the project is a service or benefits from one; a
CI workflow that runs the project's own install, test, lint, and build commands; an .env.example
listing every variable the code reads, with safe placeholder values; and short run scripts only
when they save steps.
