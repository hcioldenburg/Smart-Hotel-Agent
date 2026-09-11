"""Cheap model-config check — run BEFORE any expensive sweep.

A full sweep is ~36 conversations. If the model can't do tools, structured output, or the
sim-user model id is wrong, you want to find out in three tiny calls, not thirty-six failed
runs. This exercises exactly the three things the harness relies on:

    1. agent model: bind_tools + invoke      (does it return tool calls?)
    2. agent model: with_structured_output    (does structured decoding work?)
    3. sim  model: a plain call with temperature

Green here means a sweep will actually run. Costs a handful of tokens.

    python -m eval.preflight
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
load_dotenv(ROOT / ".env")

from smart_home_agent_backend.agent import TOOLS  # noqa: E402
from smart_home_agent_backend.utils.llm import chat_model, _is_reasoning  # noqa: E402

AGENT_MODEL = os.getenv("SMART_HOME_MODEL", "gpt-5.6-terra")
SIM_MODEL = os.getenv("SMART_HOME_SIM_MODEL", os.getenv("SMART_HOME_MODEL", "gpt-5.6-terra"))


class _Move(BaseModel):
    action: str
    reason: str


def _check(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"  PASS  {label}  {detail}")
        return True
    except Exception as exc:  # noqa: BLE001 — we want the message, not a trace
        print(f"  FAIL  {label}\n          {str(exc)[:240]}")
        return False


def main() -> int:
    print("\n══ Preflight ═══════════════════════════════════════════════")
    print(f"   agent = {AGENT_MODEL}  (reasoning={_is_reasoning(AGENT_MODEL)})")
    print(f"   sim   = {SIM_MODEL}  (reasoning={_is_reasoning(SIM_MODEL)})\n")

    ok = True

    def _tools():
        m = chat_model(AGENT_MODEL).bind_tools(TOOLS)
        r = m.invoke([{"role": "user", "content": "Use get_device_info to look up smartfan."}])
        names = [t.get("name") for t in (getattr(r, "tool_calls", []) or [])]
        return f"tool_calls={names or 'none (text reply — tools still bound OK)'}"

    def _structured():
        m = chat_model(AGENT_MODEL).with_structured_output(_Move)
        r = m.invoke([{"role": "user", "content": "Emit a Move: action=check_fan reason=verify"}])
        return f"-> action={getattr(r, 'action', '?')!r}"

    def _sim():
        m = chat_model(SIM_MODEL, temperature=0.7)
        r = m.invoke([{"role": "user", "content": "Reply with exactly: OK"}])
        return f"-> {str(getattr(r, 'content', ''))[:20]!r}"

    ok &= _check("agent bind_tools + invoke", _tools)
    ok &= _check("agent with_structured_output", _structured)
    ok &= _check("sim plain call + temperature", _sim)

    print()
    if ok:
        print("  ALL GREEN — safe to run:  python -m eval.simulate\n")
        return 0
    print("  NOT READY — fix the failures above before sweeping.")
    print("  Tips: 429 = out of quota; 400 reasoning_effort = set SMART_HOME_USE_RESPONSES_API")
    print("        or check the model id; unknown model = wrong id in .env.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
