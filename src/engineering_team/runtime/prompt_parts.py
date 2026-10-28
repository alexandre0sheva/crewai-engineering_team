"""Where a model call's prompt text comes from, for the profiler.

CrewAI reports the messages and tool schemas of each call; this sorts their size (in characters,
which the profiler turns into tokens) into the parts the profile shows:

* ``system``: system messages and the first user message (the task prompt: role text, the
  stage's instructions, the contracts it was handed);
* ``tools``: the tool schemas sent with the call (zero for a model that is given tools as text,
  where they are part of ``system``);
* ``history``: the rest of the conversation (the model's replies, later user messages);
* ``results``: what tools returned (``tool`` messages and ReAct ``Observation:`` messages).
"""

from __future__ import annotations

import json
from typing import Any

PARTS = ("system", "tools", "history", "results")
CHARS_PER_TOKEN = 4  # the usual rough conversion; the profile scales to billed tokens


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except (TypeError, ValueError):
        return str(content)


def prompt_chars(messages: Any, tools: Any) -> dict[str, int]:
    """Characters per prompt part for one call (``messages`` may be a plain string)."""

    parts = dict.fromkeys(PARTS, 0)
    if isinstance(messages, str):
        parts["system"] = len(messages)
    else:
        seen_user = False
        for message in messages or []:
            if not isinstance(message, dict):
                parts["history"] += len(_text(message))
                continue
            role = message.get("role")
            size = len(_text(message.get("content") or ""))
            if message.get("tool_calls"):
                size += len(_text(message["tool_calls"]))
            if role == "system":
                parts["system"] += size
            elif role == "tool" or (
                role == "user"
                and str(message.get("content") or "").lstrip().startswith("Observation")
            ):
                parts["results"] += size
            elif role == "user" and not seen_user:
                seen_user = True
                parts["system"] += size
            else:
                parts["history"] += size
    if tools:
        parts["tools"] = len(_text(tools))
    return parts
