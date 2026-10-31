---
name: delivery-files
description: What a Dockerfile, CI workflow, .env.example and run scripts need.
teammates: [devops_engineer]
---
# Delivery files

You read the project and its documented commands first and add only what fits: a Dockerfile (small, pinned base, non-root, no secrets baked in) when the
project is a service or benefits from one; a CI workflow that runs the project's own install, test,
lint, and build commands; an .env.example listing every variable the code reads, with safe
placeholder values; and short run scripts only when they save steps.

Find the variables the code reads by searching for the environment lookups of its language
(`os.environ`, `process.env`, `os.Getenv`, `std::env::var`, `System.getenv`). Run every command a
file you add relies on, and record in docs/devops.md what you ran and what you could not run.
