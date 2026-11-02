"""``[lsp]``: the optional language-server bridge (``docs/CONFIGURATION.md``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

LSP_LANGUAGES = ("python", "typescript", "go", "rust")


class LspSettings(BaseModel):
    """Off by default: a language server reads the project's own configuration and may run its
    build scripts (rust-analyzer does), so it is started only when asked for, through the run's
    execution backend (use the Docker sandbox for untrusted code)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    servers: dict[str, list[str]] = {}  # language -> the command to start its server (stdio)
    request_timeout_seconds: int = Field(default=30, ge=1, le=600)
    startup_timeout_seconds: int = Field(default=30, ge=1, le=600)
    diagnostics_wait_seconds: int = Field(default=10, ge=1, le=120)  # for a server that pushes
    max_diagnostic_files: int = Field(default=25, ge=1, le=500)  # files a project scan opens

    @field_validator("servers")
    @classmethod
    def _known_languages(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for language, argv in value.items():
            if language not in LSP_LANGUAGES:
                raise ValueError(
                    f"unknown language {language!r}; choose from {list(LSP_LANGUAGES)}"
                )
            if not argv or not all(isinstance(part, str) and part for part in argv):
                raise ValueError(
                    f"lsp.servers.{language} must be a command, e.g. ['name', '--stdio']"
                )
        return value
