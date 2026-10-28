"""Layered loading: defaults, user and project files, environment, overrides."""

from __future__ import annotations

import difflib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import (
    ValidationError,
)

from engineering_team.model_routing import CREDENTIAL_ENV
from engineering_team.settings.common import PROJECT_CONFIG_NAME, SettingsError
from engineering_team.settings.env import CONFIG_FILE_ENV, ENV_SETTINGS, OVERRIDES_ENV
from engineering_team.settings.masking import hide_address, mask_url
from engineering_team.settings.model import Settings
from engineering_team.settings.sections import WEB_KEY_ENV

# --- layered loading ------------------------------------------------------------------------


def _parse_overrides(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SettingsError(f"{OVERRIDES_ENV} is not valid JSON: {exc}.") from None
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise SettingsError(f"{OVERRIDES_ENV} must be a JSON object of dotted setting keys.")
    return data


def _nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    """``{"a.b": 1}`` -> ``{"a": {"b": 1}}``."""

    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        node = nested
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return nested


def _normalise(layer: dict[str, Any]) -> dict[str, Any]:
    """Expand ``roles.x = "provider/model"`` shorthand so leaf paths are uniform."""

    models = layer.get("models")
    if isinstance(models, dict) and isinstance(models.get("roles"), dict):
        models["roles"] = {
            role: {"model": spec} if isinstance(spec, str) else spec
            for role, spec in models["roles"].items()
        }
    return layer


def _merge(
    target: dict[str, Any],
    layer: Mapping[str, Any],
    label: str,
    sources: dict[str, str],
    prefix: str = "",
) -> None:
    for key, value in layer.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and isinstance(target.get(key), dict | type(None)):
            target.setdefault(key, {})
            _merge(target[key], value, label, sources, f"{path}.")
        else:
            target[key] = value
            sources[path] = label


def _build(
    layers: list[tuple[str, dict[str, Any]]],
    credential_names: frozenset[str],
    web_secrets: Mapping[str, str] | None = None,
) -> Settings:
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for label, layer in layers:
        _merge(merged, _normalise(_deep_copy(layer)), label, sources)
    try:
        settings = Settings.model_validate(merged)
    except ValidationError as exc:
        raise _friendly(exc, sources) from None
    settings._layers = layers
    settings._sources = sources
    settings._credential_names = credential_names
    settings._web_secrets = dict(web_secrets or {})
    return settings


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def _friendly(exc: ValidationError, sources: Mapping[str, str]) -> SettingsError:
    messages = []
    for error in exc.errors():
        path = ".".join(str(part) for part in error["loc"])
        if not path:  # a cross-field validator on the whole settings object
            messages.append(error["msg"].removeprefix("Value error, "))
            continue
        origin = _origin(path, sources)
        if error["type"] == "extra_forbidden":
            hint = ""
            if "." not in path:
                close = difflib.get_close_matches(path, Settings.model_fields, n=1)
                hint = f" Did you mean '{close[0]}'?" if close else ""
            messages.append(f"Unknown setting '{path}'{origin}.{hint}")
        else:
            message = error["msg"].removeprefix("Value error, ")
            given = error.get("input")
            shown = f" = {given!r}" if isinstance(given, str | int | float) else ""
            messages.append(f"Invalid setting '{path}'{shown}: {message}{origin}.")
    return SettingsError(" ".join(messages))


def _origin(path: str, sources: Mapping[str, str]) -> str:
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        source = sources.get(".".join(parts[:end]))
        if source:
            return f" (from {source})"
    # An unknown or invalid *table* is recorded by its leaves, e.g. "modles.roles.x".
    for key, source in sources.items():
        if key.startswith(f"{path}."):
            return f" (from {source})"
    return ""


def _read_toml(path: Path, label: str) -> tuple[str, dict[str, Any]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path} is not valid TOML: {exc}") from None
    except OSError as exc:
        raise SettingsError(f"Cannot read {path}: {exc.strerror or exc}") from None
    return f"{label} {path}", data


def user_config_directory() -> Path | None:
    """``~/.config/engineering-team`` (``$XDG_CONFIG_HOME`` first); ``None`` with no home."""

    try:
        return user_config_path(os.environ, Path.home()).parent
    except RuntimeError:
        return None


def user_config_path(env: Mapping[str, str], home: Path) -> Path:
    config_home = env.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else home / ".config"
    return base / "engineering-team" / "config.toml"


SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)


def child_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The process environment plus ``extra``: what a run started by the web UI inherits (it needs
    the same credentials and settings variables). Settings is the one place that reads it."""

    return {**os.environ, **(extra or {})}


def secret_values(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Values of environment variables whose names look like credentials (``*KEY*``,
    ``*TOKEN*``, ``*SECRET*``, ``*PASSWORD*``), for scrubbing them out of logs and events."""

    environment = os.environ if env is None else env
    return frozenset(
        value for name, value in environment.items() if value and SECRET_ENV_NAME.search(name)
    )


def load_settings(
    *,
    overrides: Mapping[str, Any] | None = None,
    override_source: str = "cli",
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    config_file: str | Path | None = None,
) -> Settings:
    """Load settings from every layer. ``env`` defaults to the process environment."""

    environment = os.environ if env is None else env
    working_directory = cwd or Path.cwd()
    layers: list[tuple[str, dict[str, Any]]] = []

    user_path = user_config_path(environment, home or Path.home())
    if user_path.is_file():
        layers.append(_read_toml(user_path, "user config"))

    explicit = config_file or (environment.get(CONFIG_FILE_ENV) or "").strip() or None
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = working_directory / explicit_path
        if not explicit_path.is_file():
            raise SettingsError(f"Config file not found: {explicit_path}")
        layers.append(_read_toml(explicit_path, "config file"))
    else:
        project_path = working_directory / PROJECT_CONFIG_NAME
        if project_path.is_file():
            layers.append(_read_toml(project_path, "project config"))

    for name, (key, parse) in ENV_SETTINGS.items():
        raw = environment.get(name)
        if raw is None or not raw.strip():
            continue  # blank values count as unset
        try:
            value = parse(raw)
        except ValueError as exc:
            raise SettingsError(f"Invalid value for {name}: {exc} (got {raw.strip()!r}).") from None
        layers.append((f"env {name}", _nest({key: value})))

    if raw_overrides := (environment.get(OVERRIDES_ENV) or "").strip():
        layers.append((f"env {OVERRIDES_ENV}", _nest(_parse_overrides(raw_overrides))))

    if overrides:
        layers.append((override_source, _nest(overrides)))

    credential_names = frozenset(
        name
        for names in CREDENTIAL_ENV.values()
        for name in names
        if (environment.get(name) or "").strip()
    )
    web_secrets = {
        name: value
        for name in WEB_KEY_ENV.values()
        if (value := (environment.get(name) or "").strip())
    }
    return _build(layers, credential_names, web_secrets)


def redacted_dump(settings: Settings) -> dict[str, Any]:
    """The settings as JSON for a run's record (``settings.json``): hook addresses and the
    environment given to MCP servers are hidden, URL credentials masked."""

    data: dict[str, Any] = settings.model_dump(mode="json")
    for hooks in data.get("hooks", {}).values():
        for hook in hooks:
            if hook.get("url"):
                hook["url"] = hide_address(hook["url"])
    for server in data.get("mcp", {}).values():
        if server.get("url"):
            server["url"] = mask_url(server["url"])
        server["env"] = {name: "****" for name in server.get("env", {})}
    data["docs_mcp_urls"] = [mask_url(url) for url in data.get("docs_mcp_urls", [])]
    return data
