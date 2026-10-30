"""The ``ENGINEERING_*`` environment variables and the settings each one sets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# --- environment variables ------------------------------------------------------------------


def _bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError("expected true/false")


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ValueError("expected a whole number") from None


def _float(text: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise ValueError("expected a number") from None


def _list(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def _text(text: str) -> str:
    return text.strip()


def _auto_or_int(text: str) -> str | int:
    return "auto" if text.strip().lower() == "auto" else _int(text)


def _build_env_table() -> dict[str, tuple[str, Callable[[str], Any]]]:
    table: dict[str, tuple[str, Callable[[str], Any]]] = {
        "ENGINEERING_PROVIDER": ("provider", _text),
        "ENGINEERING_RUN_PROFILE": ("profile", _text),
        "ENGINEERING_STRATEGY": ("strategy", _text),
        "ENGINEERING_TEAM_PROFILE": ("team_profile", _text),
        "ENGINEERING_PROJECT_NAME": ("project_name", _text),
        "ENGINEERING_WORKSPACE_ROOT": ("workspace_root", _text),
        "ENGINEERING_PROJECT_REQUEST": ("request", _text),
        "ENGINEERING_REQUEST_FILE": ("request_file", _text),
        "ENGINEERING_VERBOSE": ("verbose", _bool),
        "ENGINEERING_TRACING": ("tracing", _bool),
        "ENGINEERING_DOCS_MCP_URLS": ("docs_mcp_urls", _list),
        "ENGINEERING_COMMAND_ALLOWLIST": ("command_allowlist", _list),
        "ENGINEERING_SUBPROCESS_ENV_ALLOWLIST": ("subprocess_env_allowlist", _list),
        "ENGINEERING_OLLAMA_BASE_URL": ("ollama_base_url", _text),
        "ENGINEERING_ENABLE_AZURE": ("enable_azure", _bool),
        "ENGINEERING_ALLOW_WEB": ("web.enabled", _bool),
        "ENGINEERING_ALLOW_PROJECT_PLUGINS": ("allow_project_plugins", _bool),
        "ENGINEERING_CONVENTIONS_FILE": ("conventions_file", _text),
        "ENGINEERING_GIT": ("git.enabled", _bool),
        "ENGINEERING_EXECUTION_BACKEND": ("execution.backend", _text),
        "ENGINEERING_MAX_PARALLEL_COMMANDS": ("execution.max_parallel_commands", _int),
        "ENGINEERING_MAX_PARALLEL": ("parallel.max_parallel_agents", _int),
        "ENGINEERING_MAX_RPM": ("parallel.max_rpm", _int),
        "ENGINEERING_BUDGET_MAX_COST_USD": ("budget.max_cost_usd", _float),
        "ENGINEERING_BUDGET_MAX_TOKENS": ("budget.max_tokens", _int),
        "ENGINEERING_BUDGET_MAX_WALL_SECONDS": ("budget.max_wall_seconds", _int),
        "ENGINEERING_BUDGET_MAX_TOOL_CALLS": ("budget.max_tool_calls", _int),
        "ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS": ("budget.max_repair_rounds", _int),
        "ENGINEERING_LEAN_SIZE": ("lean.size", _text),
        "ENGINEERING_LEAN_CEREMONY": ("lean.ceremony", _text),
        "ENGINEERING_MAX_DELEGATIONS": ("delegation.max_delegations", _auto_or_int),
        "ENGINEERING_LAZY_TOOLS": ("tools.lazy", _bool),
    }
    # 0.1.0 model variables: the standard names only affect the standard profile and the
    # ENGINEERING_SMOKE_* names only the smoke profile.
    for profile, prefix in (("standard", "ENGINEERING_"), ("smoke", "ENGINEERING_SMOKE_")):
        for slot, name in (("lead", "LEAD"), ("worker", "WORKER")):
            base = f"profiles.{profile}.{slot}"
            table[f"{prefix}{name}_MODEL"] = (f"{base}.model", _text)
            table[f"{prefix}{name}_REASONING_EFFORT"] = (f"{base}.reasoning_effort", _text)
            table[f"{prefix}{name}_MAX_ITER"] = (f"{base}.max_iter", _int)
    return table


ENV_SETTINGS = _build_env_table()
CONFIG_FILE_ENV = "ENGINEERING_CONFIG_FILE"
# A JSON object of dotted setting keys (``{"budget.max_cost_usd": 2, "team.qa.enabled": false}``):
# how the web UI hands per-run options to the process it starts. Applied after the other
# environment variables and before command-line options; unknown keys are an error as anywhere.
OVERRIDES_ENV = "ENGINEERING_OVERRIDES"
