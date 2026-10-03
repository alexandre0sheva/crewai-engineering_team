"""Keeps docs/CONFIGURATION.md and the code that defines settings from drifting apart."""

from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path

from engineering_team.model_routing import MODEL_FACTS, PROVIDER_PRESETS
from engineering_team.pricing import build_table, default_prices
from engineering_team.settings import (
    ENV_SETTINGS,
    BrowserSettings,
    BudgetSettings,
    DevToolsSettings,
    DockerSettings,
    ExecutionSettings,
    GitSettings,
    IntakeSettings,
    KnowledgeSettings,
    NetworkSettings,
    ParallelSettings,
    RuntimeSettings,
    Settings,
    VerifySettings,
    WebSettings,
    _build,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")


def test_every_environment_variable_is_documented() -> None:
    missing = [name for name in ENV_SETTINGS if f"`{name}`" not in DOC]

    assert not missing, missing


def test_every_setting_is_documented() -> None:
    keys = set(Settings.model_fields) - {
        "models",
        "profiles",
        "budget",
        "execution",
        "parallel",
        "pricing",
        "tools",
        "runtime",
        "network",
        "web",
        "knowledge",
        "browser",
        "verify",
        "git",
        "intake",
        "team",
    }
    for section, model in (
        ("budget", BudgetSettings),
        ("execution", ExecutionSettings),
        ("execution.docker", DockerSettings),
        ("parallel", ParallelSettings),
        ("tools.dev", DevToolsSettings),
        ("runtime", RuntimeSettings),
        ("network", NetworkSettings),
        ("web", WebSettings),
        ("knowledge", KnowledgeSettings),
        ("browser", BrowserSettings),
        ("verify", VerifySettings),
        ("git", GitSettings),
        ("intake", IntakeSettings),
    ):
        keys |= {f"{section}.{name}" for name in model.model_fields}

    missing = [key for key in sorted(keys) if f"`{key}`" not in DOC]

    assert not missing, missing
    assert "[pricing." in DOC  # the override table is documented with an example


def test_every_preset_model_is_documented() -> None:
    models = {tier.model for tiers in PROVIDER_PRESETS.values() if tiers for tier in tiers.values()}

    missing = [model for model in sorted(models) if f"`{model}`" not in DOC]

    assert not missing, missing


def test_documented_model_facts_exist_for_every_openai_preset_model() -> None:
    for tier in (PROVIDER_PRESETS["openai"] or {}).values():
        assert tier.model in MODEL_FACTS, tier.model


def test_toml_examples_in_the_docs_are_valid_settings() -> None:
    blocks = re.findall(r"```toml\n(.*?)```", DOC, re.DOTALL)

    assert blocks
    for block in blocks:
        _build([("docs example", tomllib.loads(block))], frozenset()).resolved_models()


def test_only_settings_and_subprocess_code_read_the_process_environment() -> None:
    # docker.py reads only DOCKER_* and the few variables the docker client itself needs.
    allowed = {"settings.py", "commands.py", "docker.py"}
    offenders = []
    for path in (ROOT / "src" / "engineering_team").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"os\.(getenv|environ)", text) and path.name not in allowed:
            offenders.append(path.name)

    assert not offenders, offenders


def test_documented_prices_match_the_price_table() -> None:
    table = build_table()
    for model in {price.model for price in default_prices()}:
        price = table.lookup(model, on=date(2026, 10, 2))
        assert price is not None
        if price.is_free:
            continue
        row = next(line for line in DOC.splitlines() if line.startswith(f"| `{model}` |"))
        for amount in (price.input, price.output):
            assert f"${amount:.2f}" in row or f"${amount:g}" in row, (model, row)
