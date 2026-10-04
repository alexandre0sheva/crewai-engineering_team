"""The benchmark harness: tasks with hidden acceptance checks, isolated runs, and reports.

``engineering-team bench run`` runs a suite of tasks (``benchmarks/`` in a source checkout) through
the team, one subprocess per run, then judges every workspace with checks the team never saw.
See ``docs/BENCHMARKS.md`` for the method and the threat model.
"""
