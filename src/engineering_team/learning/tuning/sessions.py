"""Proposer and composer sessions: one conversation with the tuning model each, priced.

A proposer's answer becomes a candidate that the controller validates (``tree.Checker``). An
invalid one gets one retry in the same session, with the validator's lines as the reply; a second
invalid answer (or a provider failure) leaves the candidate empty, so it inherits the base. The
session's tokens are measured on the model object (before and after), as triage does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from engineering_team.learning.tuning.prompts import (
    AnswerError,
    Composition,
    Proposal,
    parse_answer,
    to_candidate,
)
from engineering_team.learning.tuning.tree import Candidate

if TYPE_CHECKING:
    from engineering_team.model_routing import ResolvedModel
    from engineering_team.settings import Settings

TUNE_ROLE = "tune"
TUNE_TIER = "lead"
MAX_OUTPUT_TOKENS = 4_000
CHARS_PER_TOKEN = 3  # conservative, as reflection's
ATTEMPTS = 2  # an invalid proposal gets one retry


@dataclass
class Session:
    """What one session produced and cost."""

    outcome: str  # proposed, no_change, invalid, failed (proposers); decided, failed (composer)
    candidate: Candidate | None = None
    composition: Composition | None = None
    attempts: int = 0
    tokens: int = 0
    cost_usd: float | None = None
    model: str = ""
    detail: str = ""
    notes: list[str] = field(default_factory=list)


def tuning_model(settings: Settings) -> ResolvedModel:
    """The proposers' and composer's model: the ``lead`` tier (``models.roles.tune`` overrides)."""

    return settings.resolve_model(TUNE_ROLE, tier=TUNE_TIER)


def build_tuning_llm(settings: Settings) -> Any:
    from engineering_team.llm import build_llm

    llm = build_llm(tuning_model(settings), settings)
    if hasattr(llm, "max_tokens"):
        object.__setattr__(llm, "max_tokens", MAX_OUTPUT_TOKENS)
    return llm


def worst_case_usd(settings: Settings, prompt_chars: int, model: str) -> float | None:
    """What a session may cost at most: every attempt with its prompt and whole output."""

    from engineering_team.pricing import estimate_cost

    prompt = prompt_chars // CHARS_PER_TOKEN + 1
    one = estimate_cost(
        settings.price_table().lookup(model),
        prompt_tokens=prompt + MAX_OUTPUT_TOKENS,  # a retry resends the first answer
        completion_tokens=MAX_OUTPUT_TOKENS,
    )
    return None if one is None else one * ATTEMPTS


class _Meter:
    """Tokens and cost of the calls made between ``__init__`` and ``read``."""

    def __init__(self, llm: Any, settings: Settings | None) -> None:
        self.llm, self.settings = llm, settings
        self.before = llm.get_token_usage_summary()

    def read(self) -> tuple[int, float | None, str]:
        after = self.llm.get_token_usage_summary()
        prompt = after.prompt_tokens - self.before.prompt_tokens
        completion = after.completion_tokens - self.before.completion_tokens
        cached = after.cached_prompt_tokens - self.before.cached_prompt_tokens
        model = str(getattr(self.llm, "model", "") or "")
        provider = getattr(self.llm, "provider", None)
        if "/" not in model and isinstance(provider, str) and provider:
            model = f"{provider}/{model}"
        cost = None
        if self.settings is not None:
            from engineering_team.pricing import estimate_cost

            cost = estimate_cost(
                self.settings.price_table().lookup(model),
                prompt_tokens=prompt,
                completion_tokens=completion,
                cached_prompt_tokens=cached,
            )
        if prompt + completion == 0:
            cost = 0.0
        return prompt + completion, cost, model


def propose(
    llm: Any,
    messages: list[dict[str, str]],
    label: str,
    kind: Literal["inv", "dom"],
    problems: Callable[[Candidate], list[str]],
    settings: Settings | None = None,
    scrub: Callable[[str], str] = str,
) -> Session:
    """One proposer session; the candidate is empty unless a valid proposal came back. A retry
    resends the first answer through ``scrub`` (the leak guard's redaction)."""

    meter = _Meter(llm, settings)
    conversation = list(messages)
    session = Session(outcome="failed", candidate=Candidate(label, kind))
    for attempt in range(1, ATTEMPTS + 1):
        session.attempts = attempt
        try:
            raw = llm.call(conversation, response_model=Proposal)
        except Exception as exc:  # a provider failure: the candidate inherits the base
            session.outcome, session.detail = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
            break
        try:
            proposal = parse_answer(raw, Proposal)
        except AnswerError as exc:
            found = [str(exc)]
        else:
            candidate = to_candidate(proposal, label, kind)
            session.notes = [proposal.notes.strip()] if proposal.notes.strip() else []
            if candidate.empty:
                session.outcome, session.detail = "no_change", "the proposer changed nothing"
                break
            found = problems(candidate)
            if not found:
                session.outcome, session.candidate = "proposed", candidate
                session.detail = f"changes {', '.join(candidate.paths())}"
                break
        session.outcome = "invalid"
        session.detail = "; ".join(found)[:600]
        if attempt == ATTEMPTS:
            session.detail = f"invalid twice, so it inherits the base: {session.detail}"
            break
        conversation += [
            {"role": "assistant", "content": scrub(_text(raw))},
            {
                "role": "user",
                "content": "The controller could not use that proposal:\n"
                + "\n".join(f"- {line}" for line in found)
                + "\nAnswer again with a corrected JSON object (or no edits).",
            },
        ]
    session.tokens, session.cost_usd, session.model = meter.read()
    return session


def compose(llm: Any, messages: list[dict[str, str]], settings: Settings | None = None) -> Session:
    """One composer session; ``composition`` is ``None`` when it failed (the base is kept)."""

    meter = _Meter(llm, settings)
    session = Session(outcome="failed", attempts=1)
    try:
        session.composition = parse_answer(
            llm.call(messages, response_model=Composition), Composition
        )
        session.outcome = "decided"
        session.detail = session.composition.decision
    except Exception as exc:  # a provider failure or a malformed answer: keep the base
        session.detail = f"{type(exc).__name__}: {str(exc)[:200]}"
    session.tokens, session.cost_usd, session.model = meter.read()
    return session


def _text(raw: Any) -> str:
    if hasattr(raw, "model_dump_json"):
        return str(raw.model_dump_json())
    return str(raw)
