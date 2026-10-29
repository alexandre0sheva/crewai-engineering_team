"""Claude Code: ``claude -p`` with ``--output-format json`` (one result document, with its cost)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from engineering_team.bench.agents.base import Billing, CliAgent, Report, count, json_objects

API_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
           "CLAUDE_CODE_USE_FOUNDRY")  # fmt: skip


class ClaudeCode(CliAgent):
    name = "claude-code"
    binary = "claude"
    caps_cost = True  # --max-budget-usd

    def detect_billing(self, env: Mapping[str, str]) -> Billing:
        """An API key (or a cloud provider) means per-token billing; without one Claude Code is
        signed in with a plan."""

        return "api" if any(env.get(name) for name in API_ENV) else "subscription"

    def arguments(self, request: str, workspace: Path) -> list[str]:
        args = [
            "-p", request, "--output-format", "json", "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
        ]  # fmt: skip
        if self.model:
            args += ["--model", self.model]
        if self.max_cost_usd is not None:
            args += ["--max-budget-usd", f"{self.max_cost_usd:.4f}"]
        return args

    def parse(self, output: str) -> Report:
        results = [o for o in json_objects(output) if o.get("type") == "result"]
        if not results:
            return Report()
        result = results[-1]
        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        cached = count(usage.get("cache_read_input_tokens"))
        prompt = count(usage.get("input_tokens")) + cached
        prompt += count(usage.get("cache_creation_input_tokens"))
        cost = result.get("total_cost_usd")
        models = result.get("modelUsage")
        failed = result.get("is_error") or result.get("subtype") not in (None, "success")
        return Report(
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            prompt_tokens=prompt, completion_tokens=count(usage.get("output_tokens")),
            cached_prompt_tokens=cached,
            models=tuple(sorted(models)) if isinstance(models, dict) else (),
            turns=count(result.get("num_turns")),
            error=f"{result.get('subtype') or 'error'}: {str(result.get('result'))[:200]}"
            if failed
            else "",
        )  # fmt: skip
