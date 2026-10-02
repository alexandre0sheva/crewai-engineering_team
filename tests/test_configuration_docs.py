"""Keeps docs/CONFIGURATION.md and the code that defines settings from drifting apart."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from engineering_team.model_routing import MODEL_FACTS, PROVIDER_PRESETS
from engineering_team.settings import (
    ENV_SETTINGS,
    BudgetSettings,
    ExecutionSettings,
    ParallelSettings,
    Settings,
    _build,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")


def test_every_environment_variable_is_documented() -> None:
    missing = [name for name in ENV_SETTINGS if f"`{name}`" not in DOC]

    assert not missing, missing


def test_every_setting_is_documented() -> None:
    keys = set(Settings.model_fields) - {"models", "profiles", "budget", "execution", "parallel"}
    for section, model in (
        ("budget", BudgetSettings),
        ("execution", ExecutionSettings),
        ("parallel", ParallelSettings),
    ):
        keys |= {f"{section}.{name}" for name in model.model_fields}

    missing = [key for key in sorted(keys) if f"`{key}`" not in DOC]

    assert not missing, missing


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
    allowed = {"settings.py", "workspace_tools.py"}
    offenders = []
    for path in (ROOT / "src" / "engineering_team").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"os\.(getenv|environ)", text) and path.name not in allowed:
            offenders.append(path.name)

    assert not offenders, offenders


def test_documented_prices_match_the_code() -> None:
    for model, facts in MODEL_FACTS.items():
        if facts.price:
            row = next(line for line in DOC.splitlines() if line.startswith(f"| `{model}` |"))
            for amount in facts.price:
                assert f"${amount:.2f}" in row or f"${amount:g}" in row, (model, row)
