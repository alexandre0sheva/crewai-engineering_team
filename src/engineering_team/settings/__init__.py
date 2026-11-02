"""Typed, layered settings: one place that reads configuration.

Precedence, highest first: explicit overrides (CLI/API) > environment (``ENGINEERING_*``,
every 0.1.0 name still works) > project ``engineering-team.toml`` > user
``~/.config/engineering-team/config.toml`` > built-in defaults. Every value remembers which
layer set it, so ``describe()`` can explain the effective configuration.

This is the only package that reads the process environment for configuration (workspace
code reads it only to build the child-process environment).

``from engineering_team.settings import Settings`` (and every other name below) keeps working:

* ``common``: constants and ``SettingsError``
* ``sections``: one typed model per ``[table]`` of the configuration
* ``model_section``: the ``[models]`` table (overrides, routes, fallbacks)
* ``model``: ``Settings`` itself (cross-section validation, model routing)
* ``describe``: ``Settings.describe`` (``config show``)
* ``openrouter``, ``context_pack``, ``lsp``: the ``[openrouter]``, ``[context]``, ``[lsp]`` tables
* ``masking``: showing values without showing secrets
* ``env``: the ``ENGINEERING_*`` variables and the settings each one sets
* ``loading``: layering defaults, files, environment and overrides into one ``Settings``
"""

from engineering_team.settings.common import (
    AZURE_DISABLED,
    DEFAULT,
    PROJECT_CONFIG_NAME,
    SMOKE_PROFILE_MARKER,
    Effort,
    SettingsError,
)
from engineering_team.settings.context_pack import ContextSettings
from engineering_team.settings.describe import SettingRow
from engineering_team.settings.env import CONFIG_FILE_ENV, ENV_SETTINGS, OVERRIDES_ENV
from engineering_team.settings.loading import (
    SECRET_ENV_NAME,
    child_environment,
    load_settings,
    redacted_dump,
    secret_values,
    user_config_directory,
    user_config_path,
)
from engineering_team.settings.lsp import LspSettings
from engineering_team.settings.masking import hide_address, mask_url, mask_value
from engineering_team.settings.model import Settings
from engineering_team.settings.model_section import ModelOverride, ModelRoute, ModelsSettings
from engineering_team.settings.sections import (
    SEARCH_PROVIDERS,
    TEAM_KEY,
    WEB_KEY_ENV,
    AcceptanceSettings,
    AnalysisSettings,
    AttemptsSettings,
    BrowserSettings,
    BudgetSettings,
    DelegationSettings,
    DevToolsSettings,
    DockerSettings,
    ExecutionSettings,
    FixSettings,
    GitSettings,
    IntakeSettings,
    KnowledgeSettings,
    LadderSettings,
    LeanSettings,
    LearningSettings,
    MaintainSettings,
    MemorySettings,
    NetworkSettings,
    ParallelSettings,
    PriceOverride,
    ProfileOverrides,
    ReviewSettings,
    RuntimeSettings,
    TeamOverride,
    ToolsSettings,
    TrajectorySettings,
    UiSettings,
    VerifySettings,
    WebSettings,
)

__all__ = [
    "AZURE_DISABLED",
    "CONFIG_FILE_ENV",
    "DEFAULT",
    "ENV_SETTINGS",
    "OVERRIDES_ENV",
    "PROJECT_CONFIG_NAME",
    "SEARCH_PROVIDERS",
    "SECRET_ENV_NAME",
    "SMOKE_PROFILE_MARKER",
    "TEAM_KEY",
    "WEB_KEY_ENV",
    "AcceptanceSettings",
    "AnalysisSettings",
    "ContextSettings",
    "DelegationSettings",
    "LeanSettings",
    "BrowserSettings",
    "BudgetSettings",
    "DevToolsSettings",
    "DockerSettings",
    "Effort",
    "ExecutionSettings",
    "FixSettings",
    "GitSettings",
    "IntakeSettings",
    "KnowledgeSettings",
    "AttemptsSettings",
    "LadderSettings",
    "MaintainSettings",
    "LspSettings",
    "ModelOverride",
    "ModelRoute",
    "ModelsSettings",
    "NetworkSettings",
    "ParallelSettings",
    "PriceOverride",
    "ProfileOverrides",
    "ReviewSettings",
    "RuntimeSettings",
    "SettingRow",
    "Settings",
    "SettingsError",
    "TeamOverride",
    "ToolsSettings",
    "LearningSettings",
    "MemorySettings",
    "TrajectorySettings",
    "UiSettings",
    "VerifySettings",
    "WebSettings",
    "child_environment",
    "hide_address",
    "load_settings",
    "mask_url",
    "mask_value",
    "redacted_dump",
    "secret_values",
    "user_config_directory",
    "user_config_path",
]
