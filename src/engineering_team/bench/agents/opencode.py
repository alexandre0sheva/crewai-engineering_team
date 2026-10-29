"""OpenCode: ``opencode run --format json`` (JSON events; ``step-finish`` parts carry the cost)."""

from __future__ import annotations

from pathlib import Path

from engineering_team.bench.agents.base import CliAgent, Report, count, json_objects


class OpenCode(CliAgent):
    name = "opencode"
    binary = "opencode"

    def arguments(self, request: str, workspace: Path) -> list[str]:
        args = ["run", "--format", "json", "--auto"]
        if self.model:
            args += ["-m", self.model]
        return [*args, request]

    def parse(self, output: str) -> Report:
        cost = 0.0
        priced = False
        prompt = completion = cached = turns = 0
        error = ""
        for event in json_objects(output):
            part = event.get("part")
            part = part if isinstance(part, dict) else {}
            if event.get("type") == "error":
                error = str(event.get("error") or event.get("message") or "error")[:200]
            if part.get("type") not in ("step-finish", "step_finish"):
                continue
            turns += 1
            step = part.get("cost")
            if isinstance(step, int | float) and not isinstance(step, bool):
                cost += float(step)
                priced = True
            tokens = part.get("tokens")
            tokens = tokens if isinstance(tokens, dict) else {}
            cache = tokens.get("cache")
            cache = cache if isinstance(cache, dict) else {}
            cached += count(cache.get("read"))
            prompt += count(tokens.get("input")) + count(cache.get("read"))
            prompt += count(cache.get("write"))
            completion += count(tokens.get("output")) + count(tokens.get("reasoning"))
        return Report(
            cost_usd=cost if priced else None, prompt_tokens=prompt, completion_tokens=completion,
            cached_prompt_tokens=cached, models=(self.model,) if self.model else (), turns=turns,
            error=error,
        )  # fmt: skip
