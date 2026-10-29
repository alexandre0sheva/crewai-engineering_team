"""Aider: ``aider --message`` (it prints ``Cost: $x message, $y session`` after each reply)."""

from __future__ import annotations

import re
from pathlib import Path

from engineering_team.bench.agents.base import Billing, CliAgent, Report

COST = re.compile(r"Cost:\s*\$([0-9]*\.?[0-9]+)\s*message,\s*\$([0-9]*\.?[0-9]+)\s*session")
TOKENS = re.compile(r"Tokens:\s*([0-9.,]+k?)\s*sent,\s*([0-9.,]+k?)\s*received")


def _tokens(text: str) -> int:
    """``1.2k`` is 1200: Aider rounds what it prints, so these counts are approximate."""

    number = text.lower().replace(",", "")
    scale = 1000 if number.endswith("k") else 1
    try:
        return round(float(number.rstrip("k")) * scale)
    except ValueError:
        return 0


class Aider(CliAgent):
    name = "aider"
    binary = "aider"

    def detect_billing(self, env: object) -> Billing:
        return "api"  # Aider has no plan login: it always calls a model API with a key

    def arguments(self, request: str, workspace: Path) -> list[str]:
        args = [
            "--message", request, "--yes-always", "--no-pretty", "--no-stream",
            "--no-auto-commits", "--no-check-update", "--no-analytics",
        ]  # fmt: skip
        if self.model:
            args += ["--model", self.model]
        return args

    def parse(self, output: str) -> Report:
        costs = COST.findall(output)
        tokens = TOKENS.findall(output)
        return Report(
            cost_usd=float(costs[-1][1]) if costs else None,
            prompt_tokens=sum(_tokens(sent) for sent, _ in tokens),
            completion_tokens=sum(_tokens(received) for _, received in tokens),
            models=(self.model,) if self.model else (), turns=len(tokens),
        )  # fmt: skip
