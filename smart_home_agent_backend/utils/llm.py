from __future__ import annotations

"""Single entry point for creating chat models, aware of reasoning-model quirks.

Two OpenAI facts drive this:

1. A REASONING model (gpt-5.x-terra, o-series) CANNOT combine function tools with a non-'none'
   reasoning_effort on /v1/chat/completions. This agent uses tools + structured output every
   turn, so on chat completions a reasoning model must run with reasoning OFF (effort='none') —
   and in testing that materially hurt diagnosis quality.
2. The /v1/responses API lifts that restriction: a reasoning model can reason AND call tools.

So for a reasoning model this helper routes through the Responses API with real reasoning; for
anything else (e.g. a cheaper non-reasoning sim-user) it stays on the default path untouched.

Config knobs (all optional):
  SMART_HOME_REASONING_EFFORT   effort for reasoning models (default 'medium')
  SMART_HOME_REASONING_MODEL_RE regex of model names to treat as reasoning models
  SMART_HOME_USE_RESPONSES_API  '0' to force the chat-completions fallback (tools => effort
                                'none', no reasoning) instead of the Responses API

NOTE (2026-07-17): the Responses-API path is written to langchain's documented interface but
was NOT yet validated live — the account hit its quota before a test call could run. Validate
with a bind_tools + with_structured_output call before trusting a full sweep.
"""

import os
import re
from typing import Any

from langchain.chat_models import init_chat_model

_EFFORT = os.getenv("SMART_HOME_REASONING_EFFORT", "medium")
_REASONING_RE = re.compile(
    os.getenv("SMART_HOME_REASONING_MODEL_RE", r"terra|thinking|reasoning|^o[134](\b|-)"),
    re.IGNORECASE,
)
_USE_RESPONSES = os.getenv("SMART_HOME_USE_RESPONSES_API", "1").lower() not in {
    "0", "false", "no", "",
}


# ── Call counter ──────────────────────────────────────────────────────────────────────
# Every model round-trip in this project goes through chat_model(), so wrapping the returned
# runnable lets us count API transactions per run without instrumenting each call site. Used
# by the eval harness to measure the single-call-move optimization (does the transaction count
# actually drop, and does quality hold). Harmless when unused.
_CALLS = {"n": 0}


def reset_calls() -> None:
    _CALLS["n"] = 0


def get_calls() -> int:
    return _CALLS["n"]


class _Counting:
    """Transparent proxy that counts invoke/ainvoke and re-wraps bind_tools /
    with_structured_output so the count follows the runnable through the chain."""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def invoke(self, *a: Any, **k: Any) -> Any:
        _CALLS["n"] += 1
        return self._inner.invoke(*a, **k)

    async def ainvoke(self, *a: Any, **k: Any) -> Any:
        _CALLS["n"] += 1
        return await self._inner.ainvoke(*a, **k)

    def bind_tools(self, *a: Any, **k: Any) -> "_Counting":
        return _Counting(self._inner.bind_tools(*a, **k))

    def with_structured_output(self, *a: Any, **k: Any) -> "_Counting":
        return _Counting(self._inner.with_structured_output(*a, **k))

    def __getattr__(self, name: str) -> Any:  # forward everything else unchanged
        return getattr(self._inner, name)


def _is_reasoning(model: str) -> bool:
    return bool(_REASONING_RE.search(model or ""))


def chat_model(model: str, **kwargs: Any):
    """init_chat_model, configured for the model family.

    Reasoning model: Responses API + reasoning_effort so tools/structured output work WITH
    reasoning (or, if SMART_HOME_USE_RESPONSES_API is off, chat-completions with effort='none').
    Either way temperature is dropped — reasoning models reject a non-default temperature.
    Non-reasoning model: passed through unchanged (keeps temperature for sim-user variety).
    """
    if _is_reasoning(model):
        if _USE_RESPONSES:
            kwargs.setdefault("use_responses_api", True)
            kwargs.setdefault("reasoning_effort", _EFFORT)
        else:
            kwargs.setdefault("reasoning_effort", "none")
        kwargs.pop("temperature", None)
    return _Counting(init_chat_model(model, **kwargs))
