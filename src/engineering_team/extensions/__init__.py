"""Extension points: MCP servers per teammate, repository conventions, optional CrewAI
knowledge, plugin tools, and lifecycle hooks (docs/CONFIGURATION.md, docs/SAFETY.md).

``config`` holds the settings models and imports nothing else of the package, so ``settings`` can
use it; every other module here reads a finished ``Settings``.
"""
