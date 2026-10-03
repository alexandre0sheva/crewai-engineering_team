# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub Security Advisories:
<https://github.com/alexandre0sheva/crewai-engineering_team/security/advisories/new>

Do not open a public issue for a vulnerability. Include the affected version, a minimal
reproduction, and the impact you see. You can expect an acknowledgement within a few days;
this is a volunteer-maintained project, so fix timelines are best effort.

Never include API keys, `.env` contents, or private code in a report.

## Supported versions

Only the latest released version receives fixes.

## Scope

In scope: flaws in this project's own code that let a request, a generated project, or an
agent act outside its intended boundary — for example escaping the project workspace,
reading protected files, leaking credentials into child processes, deleting data it does
not own, or a Docker sandbox (`--sandbox docker`) that mounts, passes, or allows more than
[docs/SAFETY.md](docs/SAFETY.md#docker-backend) says.

Out of scope:

- **Code written by the agents is untrusted.** Generated programs, package install scripts,
  and test commands run with your user's privileges unless you use the Docker sandbox. The
  command allowlist and path checks reduce accidents; they are not a security boundary
  against hostile code, and a container shares your kernel (a kernel or Docker vulnerability
  is out of scope). Run the orchestrator in a VM for untrusted requests or repositories.
- Vulnerabilities in CrewAI, model providers, or other dependencies (report those upstream;
  Dependabot tracks our dependency updates).
- Prompt-injection that only changes what a model says without crossing a boundary above.
