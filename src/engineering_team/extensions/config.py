"""Settings models of the extension points. Imported by ``settings``; must not import it."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME = re.compile(r"[a-z][a-z0-9_]*")
STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
# Embedding providers whose CrewAI configuration this project knows how to build.
EMBEDDER_PROVIDERS = ("openai", "ollama", "google-generativeai", "voyageai")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _is_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


class McpServer(_Frozen):
    """One MCP server (``[mcp.<name>]``): a remote ``url`` or a local ``command``.

    A teammate gets it when it lists ``mcp:<name>`` in its tool groups or is named in ``roles``.
    ``allow_tools`` limits which of the server's tools may be used (empty: all of them).
    """

    url: str | None = None
    transport: Literal["http", "sse"] = "http"  # for a url: streamable HTTP, or server-sent events
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    roles: list[str] = []
    allow_tools: list[str] = []

    @field_validator("roles")
    @classmethod
    def _role_keys(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower().replace(" ", "_") for item in value if item.strip()]
        bad = [item for item in cleaned if not NAME.fullmatch(item)]
        if bad:
            raise ValueError(f"role(s) {bad} must be teammate keys such as 'backend_engineer'")
        return cleaned

    @model_validator(mode="after")
    def _one_way_to_reach_it(self) -> McpServer:
        if (self.url is None) == (self.command is None):
            raise ValueError(
                "set exactly one of 'url' (a remote server) or 'command' (a local one)"
            )
        if self.url is not None and not _is_http_url(self.url):
            raise ValueError("'url' must be an http(s) address, for example https://host/mcp")
        if self.command is not None and not self.command.strip():
            raise ValueError("'command' must not be empty")
        if self.command is not None and self.transport != "http":
            raise ValueError("'transport' only applies to a url; a command speaks over stdio")
        if self.url is not None and (self.args or self.env):
            raise ValueError("'args' and 'env' only apply to a command")
        return self


class ConventionsSettings(_Frozen):
    """The repository's own conventions (AGENTS.md, CLAUDE.md, CONTRIBUTING.md, .editorconfig)
    given to the team when it works on an existing project. Sizes are in characters."""

    enabled: bool = True
    max_chars: int = Field(default=12_000, ge=500, le=200_000)  # all files together
    max_file_chars: int = Field(default=6_000, ge=200, le=100_000)  # any one file


class PluginSettings(_Frozen):
    """Plugin tools installed as Python packages (entry point group ``engineering_team.tools``).
    Tools from the project's own ``.engineering-team/tools/`` need ``allow_project_plugins``."""

    entry_points: bool = True
    disable: list[str] = []  # entry-point names or project file names (``word_count``) to skip


class EmbedderSettings(_Frozen):
    """The embedding model CrewAI uses for ``knowledge.sources``; names the provider on purpose."""

    provider: Literal["openai", "ollama", "google-generativeai", "voyageai"]
    model: str | None = None
    url: str | None = None  # ollama: the embeddings endpoint (default http://localhost:11434/...)


class Hook(_Frozen):
    """One hook: a ``command`` (argument list, no shell) or a webhook ``url`` that gets a JSON
    POST. ``stages`` limits stage hooks to those stages, ``statuses`` to those outcomes."""

    command: list[str] | None = None
    url: str | None = None
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    stages: list[str] = []
    statuses: list[Literal["succeeded", "failed", "cancelled", "interrupted"]] = []

    @field_validator("stages")
    @classmethod
    def _stage_names(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        bad = [item for item in cleaned if not NAME.fullmatch(item)]
        if bad:
            raise ValueError(f"stage name(s) {bad} must look like 'implement' or 'verify'")
        return cleaned

    @model_validator(mode="after")
    def _command_or_url(self) -> Hook:
        if (self.command is None) == (self.url is None):
            raise ValueError("set exactly one of 'command' (a program and its arguments) or 'url'")
        if self.command is not None and (not self.command or not self.command[0].strip()):
            raise ValueError("'command' must start with the program to run")
        if self.url is not None and not _is_http_url(self.url):
            raise ValueError("'url' must be an http(s) address")
        return self


class HooksSettings(_Frozen):
    """Commands and webhooks run at ``before_stage``, ``after_stage`` and ``on_finish``."""

    before_stage: list[Hook] = []
    after_stage: list[Hook] = []
    on_finish: list[Hook] = []

    @model_validator(mode="after")
    def _filters_fit_the_event(self) -> HooksSettings:
        if any(hook.statuses for hook in self.before_stage):
            raise ValueError("before_stage hooks run before there is a status; drop 'statuses'")
        if any(hook.stages for hook in self.on_finish):
            raise ValueError("on_finish hooks are not about one stage; drop 'stages'")
        return self

    @property
    def any(self) -> bool:
        return bool(self.before_stage or self.after_stage or self.on_finish)
