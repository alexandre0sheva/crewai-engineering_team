"""Codex: ``codex exec --json`` (JSONL events; the cost is the reported tokens times our prices)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from engineering_team.bench.agents.base import Billing, CliAgent, Report, count, json_objects
from engineering_team.pricing import estimate_cost

API_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")


def _usage_in(event: dict[str, object]) -> dict[str, object] | None:
    usage = event.get("usage")
    if isinstance(usage, dict) and ("input_tokens" in usage or "output_tokens" in usage):
        return usage
    return None


class Codex(CliAgent):
    name = "codex"
    binary = "codex"

    def detect_billing(self, env: Mapping[str, str]) -> Billing:
        return "api" if any(env.get(name) for name in API_ENV) else "subscription"

    def arguments(self, request: str, workspace: Path) -> list[str]:
        args = [
            "exec", "--json", "--color", "never", "--skip-git-repo-check", "--ephemeral",
            "--sandbox", "danger-full-access", "-C", str(workspace),
        ]  # fmt: skip
        if self.model:
            args += ["-m", self.model]
        return [*args, "--", request]

    def parse(self, output: str) -> Report:
        events = json_objects(output)
        prompt = completion = cached = turns = 0
        for event in events:
            usage = _usage_in(event) if event.get("type") == "turn.completed" else None
            if usage is not None:
                prompt += count(usage.get("input_tokens"))
                completion += count(usage.get("output_tokens"))
                cached += count(usage.get("cached_input_tokens"))
                turns += 1
        failures = [
            str(e.get("message") or e.get("error") or e.get("type"))
            for e in events
            if e.get("type") in ("turn.failed", "error")
        ]
        cost = None
        if self.model and turns:
            cost = estimate_cost(
                self.prices.lookup(self.model), prompt_tokens=prompt,
                completion_tokens=completion, cached_prompt_tokens=cached,
            )  # fmt: skip
        return Report(
            cost_usd=cost, prompt_tokens=prompt, completion_tokens=completion,
            cached_prompt_tokens=cached, models=(self.model,) if self.model else (), turns=turns,
            error=failures[-1][:200] if failures else "",
        )  # fmt: skip
