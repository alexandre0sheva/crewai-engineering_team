"""``[context]``: how much of the repository, memory and lessons a stage is told up front."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextSettings(BaseModel):
    """Context packs (``docs/CONFIGURATION.md``). Provisional defaults until the 0.3.0
    evaluation (plan T33)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True  # put a map of the files that matter for the request in each pack
    stage_tokens: int = Field(default=2500, ge=200, le=100_000)  # a pack's size, in tokens
    stages: dict[str, int] = {}  # a stage's own pack size, by stage prompt name (``review``, ...)
    recency_days: int = Field(default=90, ge=0, le=3650)  # Git history that counts; 0 ignores it
    warn_fraction: float = Field(default=0.6, gt=0, le=1)  # warn above this share of the window

    @field_validator("stages")
    @classmethod
    def _sizes(cls, value: dict[str, int]) -> dict[str, int]:
        for name, tokens in value.items():
            if not 200 <= tokens <= 100_000:
                raise ValueError(f"context.stages.{name}: {tokens} is outside 200-100000 tokens.")
        return value

    def tokens_for(self, stage: str) -> int:
        """The pack size of ``stage`` (its own entry, else ``stage_tokens``)."""

        return self.stages.get(stage, self.stage_tokens)
