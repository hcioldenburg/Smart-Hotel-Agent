"""Drive the agent through whole conversations with a SIMULATED participant.

This is the thing that turns "chat with it once and read the output" into "run it 50 times
overnight and get a report". An LLM plays the participant: it is handed the scenario briefing
plus a fact-sheet of what a person in that room would OBSERVE, and it answers the agent's
checks from that — one lay reply at a time — until the agent concludes or a turn cap is hit.
Every run is written as a normal session log (same schema as logs/chats/), so eval/report.py
and eval/grade_sessions.py score it with no special-casing.

THE ONE RULE THAT MAKES THIS VALID (mirror of scripts/check_prompt_leakage.py): the simulated
participant is a WORLD ORACLE, not an answer key. It is fed observation fields only —
symptom.participant_sees, evidence_path[].observable, observable_in_room, per_condition — and
NEVER root_cause, injected_fault.*, evidence_path[].discriminates/rules_out, distractors,
expected_conclusion or grading. If the sim-user knew the fault it would steer the agent to it
and every run would "pass" for the wrong reason — the eval would be theatre. _assert_no_leak()
enforces the boundary at build time.

    python -m eval.simulate                              # 2 runs x 9 scenarios x both modes
    python -m eval.simulate --scenarios SC-DOOR-WINDOW-OPEN-FAN-OFF --runs 3
    python -m eval.simulate --modes dashboard --runs 5 --out-dir logs/sim_runs/nightly
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from smart_home_agent_backend.agent import (  # noqa: E402
    _build_case_file,
    _serialize_messages,
    build_graph,
    initial_state,
)
from smart_home_agent_backend.utils.llm import (  # noqa: E402
    chat_model as init_chat_model,
    get_calls,
    reset_calls,
)

load_dotenv()

GROUND_TRUTH = ROOT / "configs" / "ground_truth"
DEFAULT_OUT = ROOT.parent / "logs" / "sim_runs"

# A separate env so the participant can be a cheaper/different model than the agent.
import os  # noqa: E402

SIM_MODEL = os.getenv("SMART_HOME_SIM_MODEL", os.getenv("SMART_HOME_MODEL", "gpt-5.6-terra"))

# Personas rotate across the runs of a scenario so the agent meets different real behaviours —
# a terse reporter, a chatty one, someone who blurts a wrong guess, someone who pushes back.
# Variety here is what surfaces path-dependent bugs (the wrong-hunch persona is how suspect
# elicitation and the "don't fixate on their guess" policy get exercised).
PERSONAS: dict[str, str] = {
    "concise": "You answer in as few words as possible — often just the value asked for ('Open', 'It stopped', '3 days ago'). You never volunteer theories.",
    "chatty": "You are talkative and add context ('okay I opened the window again, waited a bit, and yeah it still says open'). You stay a layperson.",
    "wrong_hunch": "Early on, when asked what you think is wrong, you confidently blame the WRONG thing (usually the device that is visibly misbehaving). You go along with the agent's checks anyway.",
    "methodical": "You are cooperative and precise. You do exactly what is asked and report the reading accurately, nothing more.",
    "impatient": "You are a bit impatient. Roughly once you push back on a check ('why do you need that?') before doing it, and you dislike being asked the same thing twice.",
}

_SIM_SYSTEM = """You are role-playing a NON-EXPERT person in a smart-hotel room, using a tablet to work out why something is not behaving as expected. A troubleshooting assistant is helping you. You are the USER.

HOW YOU BEHAVE:
- You are not a technician. You do not know the cause, and you must never diagnose it yourself or name an error type unless you are guessing out loud as a layperson.
- When the assistant asks you to check or do something, you DO it and report ONLY what you observe, using the OBSERVATIONS below. Report it in plain, casual words.
- If asked to read a value that the observations cover, give that reading. If asked something the observations do not explicitly cover, answer in a way that stays fully consistent with them and with everything you have already said — never invent a fault that contradicts them.
- You can only observe what a person with a tablet and their own eyes could: device states/readings on the dashboard, whether a device physically responds, indicator lights on devices, what a rule's card says. You cannot see hidden wiring or "why".
- Keep replies short and human. One turn = one reply. Do not narrate stage directions.
- When the assistant gives you a final conclusion / tells you to contact a technician, briefly acknowledge ('ok, thanks') and stop.

{persona}

THE SITUATION YOU ARE REPORTING:
{briefing}

WHAT YOU OBSERVE WHEN YOU CHECK THINGS (this is your reality — answer from it, never beyond it):
{observations}
"""


def load_gt(scenario_id: str) -> dict[str, Any]:
    path = GROUND_TRUTH / f"{scenario_id}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def all_scenario_ids() -> list[str]:
    return sorted(
        p.stem for p in GROUND_TRUTH.glob("*.yaml") if not p.name.startswith("_")
    )


def _flatten(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_check(text: str) -> str:
    """The agent's-instruction half of an evidence_path step, with internal asides removed.

    Some `check` fields carry parenthetical references to other config files
    ('THE BYPASS. (Procedure: device_knowledge.yaml …)') — an experimenter note, not something
    a participant would ever see. Strip all parentheticals so no config path reaches the
    sim-user.
    """
    import re
    return " ".join(re.sub(r"\([^)]*\)", "", str(text or "")).split())


def build_factsheet(gt: dict[str, Any], mode: str) -> str:
    """The world oracle — OBSERVATION fields only. See the module docstring and _assert_no_leak.

    Built SOLELY from evidence_path[].check → observable: the curated, per-check,
    observation-pure oracle (each step is 'if asked to do X, you see Y'). Deliberately excluded:
      - injected_fault.observable_in_room — prose that mixes observation with diagnosis
        ('…so this is not a device error'); its physical observation is already an evidence_path step.
      - per_condition — it carries experimenter commentary that names the answer outright
        (SC-DOOR-WINDOW floor_map: 'the correct answer is DEVICE error'). Navigation is the
        AGENT's job anyway; the sim-user only needs to report what it sees when asked.
    `mode` is accepted for signature stability (and future per-mode observations) but no longer
    pulls in per_condition text.
    """
    lines: list[str] = []
    for step in gt.get("evidence_path", []) or []:
        check = _clean_check(step.get("check"))
        obs = _flatten(step.get("observable"))
        if check and obs:
            lines.append(f"- If asked to: {check}\n    You observe: {obs}")

    return "\n".join(lines) or "- (No specific observations provided; answer plausibly and consistently.)"


# A fact-sheet is OBSERVATIONS, never a VERDICT. Observations say what is seen ("the state does
# not move", "the light does not flash", "it responds to both"); they never name a fault type,
# call something faulty, or describe the hidden apparatus. This backstop checks for that verdict
# language directly — which is robust to the fact that observations and cause-text share plenty
# of domain vocabulary (device names, "correctly written"), where an n-gram-overlap check kept
# flagging the safe half. It caught the real leak that started this: an observable_in_room field
# that read "…so this is not a device error" (now excluded — see build_factsheet).
_VERDICT_PHRASES = (
    "device error", "connection error", "configuration error", "config error",
    "device fault", "connection fault", "configuration fault",
    "is faulty", "is broken", "is dead", "misconfigured", "mis-configured",
    "the fault is", "the cause is", "root cause", "the real cause", "the real problem",
    "being intercepted", "interception", "interceptor", "fake indicator", "fake light",
    "simulated", "hidden apparatus", "the answer is", "diagnos",
)


def _assert_no_leak(factsheet: str, gt: dict[str, Any]) -> None:
    hay = " ".join(factsheet.lower().split())
    for phrase in _VERDICT_PHRASES:
        if phrase in hay:
            raise SystemExit(
                f"LEAK GUARD: the sim-user fact-sheet contains verdict language: '{phrase}'. "
                "The participant must be fed OBSERVATIONS only, never a diagnosis. Check the "
                "evidence_path[].observable / per_condition fields for this scenario."
            )


def _sim_user(model, factsheet: str, persona_key: str, briefing: str,
              transcript: list[dict], agent_msg: str | None) -> str:
    """One participant turn. agent_msg=None means produce the opening message."""
    system = _SIM_SYSTEM.format(
        persona=PERSONAS[persona_key], briefing=briefing, observations=factsheet
    )
    convo = [{"role": "system", "content": system}]
    # Replay the conversation from the PARTICIPANT's seat: the agent is the "user" to the
    # sim-user model, and the sim-user's past lines are the "assistant".
    for m in transcript:
        role = "assistant" if m["role"] == "user" else "user"
        if m.get("content"):
            convo.append({"role": role, "content": m["content"]})
    if agent_msg is None:
        convo.append({
            "role": "user",
            "content": "Start the conversation: tell the assistant, in your own casual words, what problem you noticed.",
        })
    resp = model.invoke(convo)
    return str(getattr(resp, "content", "") or "").strip()


# ── Participants ───────────────────────────────────────────────────────────────────────
# Two ways to answer the agent's checks. The LLM participant (default) is natural and varied —
# good for distribution testing across personas. The SCRIPTED participant is deterministic and
# FREE (no model calls): it matches each agent request to the nearest evidence_path check and
# returns that observation. Scripted mode makes runs reproducible (run once, no K-repeats for
# noise) and removes the sim-user's ~third of the per-run transactions — the cheap-eval path.

_STOP = frozenset(
    "the a an and or to of in on at for me you your it its is are be been do does did with that "
    "this what which whether just so we can see if read tell find open go now please then there "
    "here right ok okay good thanks let watch look me my i still once more too also its".split()
)


def _content_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", str(text or "").lower()) if w not in _STOP}


# The agent's elicitation / "what do you think?" turn — a scripted participant should decline a
# diagnosis (it must never volunteer the answer), not try to answer it as a check.
_ELICIT_RE = re.compile(
    r"gut feeling|what.{0,25}(causing|going on|do you (think|reckon))|your (guess|hunch|suspicion|theory)"
    r"|tried anything|what stands out|any (ideas?|guess)",
    re.IGNORECASE,
)


def _derive_opener(gt: dict[str, Any]) -> str:
    """First-person symptom for the opening turn, from participant_sees with the task-instruction
    tail stripped and a light 2nd→1st person swap."""
    text = _flatten((gt.get("symptom") or {}).get("participant_sees"))
    for marker in ("using the tablet", "your task", "please think", "please try to determine", "let us know"):
        i = text.lower().find(marker)
        if i > 0:
            text = text[:i]
            break
    for a, b in (("You ", "I "), ("you ", "I "), ("Your ", "My "), ("your ", "my "),
                 ("You're", "I'm"), ("you're", "I'm")):
        text = text.replace(a, b)
    text = " ".join(text.split())
    return text[:400] or "Something in the room isn't behaving the way I expected."


class ScriptedParticipant:
    """Deterministic, zero-cost participant. Answers from evidence_path observations by best
    keyword match; declines the diagnosis question; falls back to a neutral non-answer."""

    kind = "scripted"

    def __init__(self, gt: dict[str, Any]) -> None:
        self._opener = _derive_opener(gt)
        self._checks: list[tuple[set[str], str]] = []
        for step in gt.get("evidence_path", []) or []:
            chk = _content_tokens(_clean_check(step.get("check")))
            obs = _flatten(step.get("observable"))
            if chk and obs:
                self._checks.append((chk, obs))

    def opening(self, transcript: list[dict]) -> str:
        return self._opener

    def reply(self, agent_msg: str, transcript: list[dict]) -> str:
        low = (agent_msg or "").lower()
        if _ELICIT_RE.search(low):
            return ("I'm not sure what's causing it — that's what I'm hoping to work out. "
                    "I haven't tried anything myself yet.")
        atoks = _content_tokens(agent_msg)
        best, score = "", 0.0
        for ctoks, obs in self._checks:
            s = len(atoks & ctoks) / len(ctoks)  # fraction of the check's content words the agent named
            if s > score:
                best, score = obs, s
        if best and score >= 0.25:
            return best
        return "It looks the same as before — I don't see anything unusual there."


class LLMParticipant:
    """The natural, varied participant — one model call per turn."""

    kind = "llm"

    def __init__(self, model: Any, factsheet: str, persona: str, briefing: str) -> None:
        self._m, self._fs, self._p, self._b = model, factsheet, persona, briefing

    def opening(self, transcript: list[dict]) -> str:
        return _sim_user(self._m, self._fs, self._p, self._b, transcript, None)

    def reply(self, agent_msg: str, transcript: list[dict]) -> str:
        return _sim_user(self._m, self._fs, self._p, self._b, transcript, agent_msg)


def run_one(graph, sid: str, mode: str, persona: str, turn_cap: int,
            out_dir: Path, scripted: bool = False) -> dict[str, Any]:
    gt = load_gt(sid)
    briefing = _flatten((gt.get("symptom") or {}).get("participant_sees"))
    factsheet = build_factsheet(gt, mode)
    _assert_no_leak(factsheet, gt)  # holds for both participant kinds — observations only

    session_id = str(uuid.uuid4())
    state = initial_state(active_scenario_id=sid, presentation_mode=mode)
    state["session_id"] = session_id

    if scripted:
        participant: Any = ScriptedParticipant(gt)
        persona = "scripted"
    else:
        participant = LLMParticipant(init_chat_model(SIM_MODEL, temperature=0.7),
                                     factsheet, persona, briefing)

    transcript: list[dict] = []
    reset_calls()  # count model transactions this run (agent + evaluator [+ sim if LLM])
    user_msg = participant.opening(transcript)

    stop = "cap"
    for _ in range(turn_cap):
        transcript.append({"role": "user", "content": user_msg})
        state["messages"] = [*state.get("messages", []), {"role": "user", "content": user_msg}]
        state = graph.invoke(state, config={"recursion_limit": 10})

        agent_msg = str(state.get("last_agent_reply", "") or "")
        transcript.append({"role": "assistant", "content": agent_msg})

        if state.get("issue_identified"):
            stop = "resolved"
            break
        user_msg = participant.reply(agent_msg, transcript)

    record = {
        "session_id": session_id,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scenario_id": sid,
        "presentation_mode": mode,
        "reported_symptom": state.get("reported_symptom"),
        "observed_strategy": state.get("observed_strategy"),
        "issue_identified": state.get("issue_identified", False),
        "diagnostic_checklist": state.get("diagnostic_checklist", []),
        "messages": _serialize_messages(state.get("messages", [])),
        "case_file": _build_case_file(state),
        "evaluator_log": state.get("evaluator_log", []),
        # Sim-only metadata (ignored by grade_sessions, handy in the report). model_calls is
        # total transactions this run (agent + evaluator + sim) — the number the single-call
        # optimization is meant to reduce.
        "sim": {
            "persona": persona,
            "stop_reason": stop,
            "sim_model": ("scripted" if scripted else SIM_MODEL),
            "participant": ("scripted" if scripted else "llm"),
            "model_calls": get_calls(),
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{session_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the agent against a simulated participant.")
    ap.add_argument("--scenarios", nargs="*", help="scenario ids (default: all)")
    ap.add_argument("--modes", nargs="*", default=["dashboard", "floor_map"],
                    choices=["dashboard", "floor_map"])
    ap.add_argument("--runs", type=int, default=2, help="runs per scenario per mode")
    ap.add_argument("--turn-cap", type=int, default=14)
    ap.add_argument("--scripted", action="store_true",
                    help="deterministic FREE participant (no sim-user model calls; reproducible)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="default: logs/sim_runs/<timestamp>/")
    args = ap.parse_args()

    scenarios = args.scenarios or all_scenario_ids()
    out_dir = args.out_dir or (DEFAULT_OUT / dt.datetime.now().strftime("%Y%m%dT%H%M%S"))
    persona_keys = list(PERSONAS)

    graph = build_graph()
    total = len(scenarios) * len(args.modes) * args.runs
    sim_label = "scripted (deterministic, free)" if args.scripted else SIM_MODEL
    print(f"Simulating {total} run(s) -> {out_dir}\n  agent={os.getenv('SMART_HOME_MODEL','gpt-5.6-terra')}  sim={sim_label}\n")

    done = 0
    for sid in scenarios:
        for mode in args.modes:
            for i in range(args.runs):
                persona = "scripted" if args.scripted else persona_keys[i % len(persona_keys)]
                done += 1
                tag = f"[{done}/{total}] {sid} · {mode} · {persona}"
                try:
                    rec = run_one(graph, sid, mode, persona, args.turn_cap, out_dir,
                                  scripted=args.scripted)
                    turns = sum(1 for m in rec["messages"] if m["role"] == "assistant")
                    flag = "OK " if rec["issue_identified"] else "unresolved"
                    print(f"  {tag} -> {flag} ({turns} agent turns, {rec['sim']['stop_reason']})")
                except SystemExit:
                    raise
                except Exception as exc:  # a single run must never sink the batch
                    print(f"  {tag} -> ERROR: {exc}")

    print(f"\nDone. {total} run(s) in {out_dir}\n  Score them:  python -m eval.report --dir {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
