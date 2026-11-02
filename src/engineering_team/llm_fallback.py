"""Moving to another model when the one a tier uses keeps failing.

CrewAI retries a rate limit itself (three attempts with backoff) and has no fallback, so a model
that is down or throttled ends the stage. :func:`attach_fallbacks` wraps the ``call`` and
``acall`` of an LLM: when the call still fails with a provider error, a rate limit or a timeout,
the next model of the tier's ``models.fallbacks`` answers instead, and a ``model.fallback`` event
records it (the fallback's tokens and cost are the run's ordinary ``llm.call`` usage of that
model). A model that failed is left alone for ``cooldown_seconds`` so the next calls do not each
wait out its retries again. Errors that a second model would not cure (bad request, invalid key,
a budget stop, a cancel) are raised as they are.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

# Status codes that mean "the provider, not the request": throttled, timed out, or broken.
FALLBACK_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
# Status codes a second model will not cure: the request or the key is wrong.
REQUEST_STATUS = frozenset({400, 401, 403, 404, 413, 422})
RATE_LIMIT_MARKERS = ("rate limit", "rate-limit", "too many requests", "quota exceeded")
TIMEOUT_MARKERS = ("timed out", "timeout")
OUTAGE_MARKERS = (
    "overloaded",
    "service unavailable",
    "temporarily unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "connection error",
    "connection reset",
    "connection refused",
)
MAX_ERROR_CHARS = 300

Notify = Callable[..., None]


def _chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def failure_kind(error: BaseException) -> str | None:
    """``rate_limit``, ``timeout`` or ``provider_error`` when a fallback model may succeed where
    ``error`` failed; ``None`` for any other error."""

    errors = list(_chain(error))
    statuses = [s for s in map(_status, errors) if s is not None]
    if any(s in REQUEST_STATUS for s in statuses):
        return None
    if 429 in statuses:
        return "rate_limit"
    if 408 in statuses or 504 in statuses:
        return "timeout"
    if any(s in FALLBACK_STATUS for s in statuses):
        return "provider_error"
    for candidate in errors:
        name = type(candidate).__name__.lower()
        text = str(candidate).lower()
        if "ratelimit" in name or any(m in text for m in RATE_LIMIT_MARKERS):
            return "rate_limit"
        if isinstance(candidate, TimeoutError) or "timeout" in name:
            return "timeout"
        if any(m in text for m in TIMEOUT_MARKERS):
            return "timeout"
        if isinstance(candidate, ConnectionError) or any(
            part in name for part in ("connection", "overloaded", "unavailable", "internalserver")
        ):
            return "provider_error"
        if any(m in text for m in OUTAGE_MARKERS):
            return "provider_error"
    return None


@dataclass
class _Target:
    model: str
    build: Callable[[], Any]
    llm: Any = None
    failed: Exception | None = None  # building it failed (a missing key or SDK)


@dataclass
class FallbackChain:
    """The primary model and the models that stand in for it, in order."""

    primary: str
    targets: list[_Target]
    cooldown_seconds: float
    notify: Notify
    clock: Callable[[], float] = time.monotonic
    _cool_until: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _configure: list[Callable[[Any], None]] = field(default_factory=list)

    def on_build(self, configure: Callable[[Any], None]) -> None:
        """Apply ``configure`` to every fallback LLM, now (those built) and when it is built."""

        self._configure.append(configure)
        for target in self.targets:
            if target.llm is not None:
                configure(target.llm)

    def llm_of(self, target: _Target) -> Any:
        if target.llm is None:
            if target.failed is not None:
                raise target.failed
            try:
                llm = target.build()
            except Exception as exc:  # a missing key or SDK: this fallback cannot be used
                target.failed = exc
                raise
            for configure in self._configure:
                configure(llm)
            target.llm = llm
        return target.llm

    def cooling(self) -> bool:
        with self._lock:
            return self.clock() < self._cool_until

    def cool(self) -> None:
        with self._lock:
            self._cool_until = self.clock() + self.cooldown_seconds

    def announce(self, target: _Target, reason: str, error: Exception | None, kwargs: Any) -> None:
        agent = getattr(kwargs.get("from_agent"), "role", None)
        data: dict[str, Any] = {
            "from_model": self.primary,
            "to_model": target.model,
            "reason": reason,
        }
        if error is not None:
            data["error"] = f"{type(error).__name__}: {str(error)[:MAX_ERROR_CHARS]}"
        try:
            self.notify("model.fallback", agent=str(agent) if agent else None, **data)
        except Exception:  # recording must never break the call
            return


def attach_fallbacks(
    llm: Any,
    fallbacks: Sequence[tuple[str, Callable[[], Any]]],
    *,
    primary: str,
    cooldown_seconds: float = 60.0,
    notify: Notify | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FallbackChain:
    """Make ``llm.call`` and ``llm.acall`` move to ``fallbacks`` (``(model, build)`` pairs; a
    fallback is built when first needed) when the primary fails in a way another model may cure."""

    if notify is None:
        from engineering_team.runtime.bridge import emit_current

        notify = emit_current
    chain = FallbackChain(
        primary=primary,
        targets=[_Target(model, build) for model, build in fallbacks],
        cooldown_seconds=cooldown_seconds,
        notify=notify,
        clock=clock,
    )
    primary_call, primary_acall = llm.call, llm.acall

    def call(*args: Any, **kwargs: Any) -> Any:
        return _run(chain, primary_call, "call", args, kwargs)

    async def acall(*args: Any, **kwargs: Any) -> Any:
        return await _arun(chain, primary_acall, "acall", args, kwargs)

    object.__setattr__(llm, "call", call)
    object.__setattr__(llm, "acall", acall)
    object.__setattr__(llm, "_fallback_chain", chain)
    return chain


def _order(chain: FallbackChain) -> tuple[bool, list[int]]:
    """Which models to try, as indexes (0 is the primary): after a failure the primary is left
    alone for the cooldown and tried last."""

    n = len(chain.targets)
    if chain.cooling():
        return True, [*range(1, n + 1), 0]
    return False, list(range(n + 1))


def _run(
    chain: FallbackChain, primary: Callable[..., Any], method: str, args: Any, kwargs: Any
) -> Any:
    cooling, order = _order(chain)
    reason, last = ("cooldown" if cooling else ""), None
    for index in order:
        if index == 0:
            call = primary
        else:
            target = chain.targets[index - 1]
            try:
                call = getattr(chain.llm_of(target), method)
            except Exception:
                continue
        try:
            result = call(*args, **kwargs)
        except Exception as exc:
            kind = failure_kind(exc)
            if kind is None:
                raise
            if index == 0:
                chain.cool()
            reason, last = kind, exc
            continue
        if index != 0:
            chain.announce(chain.targets[index - 1], reason or "error", last, kwargs)
        return result
    assert last is not None  # the primary and every fallback failed, or no fallback was usable
    raise last


async def _arun(
    chain: FallbackChain, primary: Callable[..., Any], method: str, args: Any, kwargs: Any
) -> Any:
    cooling, order = _order(chain)
    reason, last = ("cooldown" if cooling else ""), None
    for index in order:
        if index == 0:
            call = primary
        else:
            target = chain.targets[index - 1]
            try:
                call = getattr(chain.llm_of(target), method)
            except Exception:
                continue
        try:
            result = await call(*args, **kwargs)
        except Exception as exc:
            kind = failure_kind(exc)
            if kind is None:
                raise
            if index == 0:
                chain.cool()
            reason, last = kind, exc
            continue
        if index != 0:
            chain.announce(chain.targets[index - 1], reason or "error", last, kwargs)
        return result
    assert last is not None
    raise last
