"""Free, offline regression suite for the agent's DECISION LOGIC — no API calls.

Every function under test here is pure given state (the evaluator gates, the move helpers, the
reply-option normalizer, the model-family detector, the sim-user leak guard). So this whole
suite runs in a second with zero token cost, and it pins every bug we've fixed so it can't come
back silently. Run it before spending any live budget — it is the cheap foundation under the
expensive end-to-end sweeps.

Each case encodes the ESSENCE of a real failure (session ids in comments for traceability), as
a crafted state rather than a saved log, so the suite is self-contained and always runnable.

    python -m eval.logic_checks          # PASS/FAIL per case; exit 1 if any fail
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from smart_home_agent_backend.utils.config_loader import build_compiled_config  # noqa: E402
from smart_home_agent_backend.utils.evaluator import (  # noqa: E402
    _check_duplicate,
    _check_fault_type,
    _same_check,
)
from smart_home_agent_backend.utils.nodes import (  # noqa: E402
    DiagnosticMove,
    HypothesisEntry,
    _merge_hypotheses,
    _move_from_tool_args,
    _normalize_reply_options,
)
from smart_home_agent_backend.utils.llm import _is_reasoning  # noqa: E402

_CFG = build_compiled_config(save_snapshot=False)


class _Move:
    def __init__(self, action_type, target_device_id="", fault_label=None, reply_text=""):
        self.action_type = action_type
        self.target_device_id = target_device_id
        self.fault_label = fault_label
        self.reply_text = reply_text


def _evidence(pairs):
    """Build an evidence + timeline state from (instruction, result) pairs, the way solve_node
    records answered checks."""
    tl, ev = [], []
    for instr, result, dev in pairs:
        tl.append({"action": instr, "result": result, "target_device_id": dev,
                   "action_type": "portal_check"})
        ev.append({"source": "user", "content": f"User reported (re: {instr}): {result}"})
    return {"deduction_timeline": tl, "evidence": ev, "checked_tools": []}


# ── the cases ─────────────────────────────────────────────────────────────────────────
# Each returns True on pass. Raising or returning False is a failure.

def _fault_type_cases():
    out = []

    def add(name, ok):
        out.append((name, ok))

    ft = lambda mv, st: bool(_check_fault_type(mv, st, _CFG))  # noqa: E731

    # 4abbad5c: rule MATCHES expectation AND is stale (3d ago) -> config must be BLOCKED (device).
    # The match acknowledgement ("that matches what you expected") is what makes it the trap —
    # present verbatim in the real transcript's agent turn.
    st = _evidence([
        ("read the fan rule Conditions and Actions", "trigger window open, condition door open, action turn off fan", "smartfan"),
        ("That matches what you expected. Read the rule's Last triggered value", "it shows 3d ago", "smartfan"),
    ])
    add("4abbad5c matched+stale -> block config", ft(_Move("conclude", "windowsensor", "configuration_error"), st) is True)

    # TV-BEDLIGHT: rule RAN and does an INCOMPLETE action ("only commands Bedlight Right") ->
    # genuine config fault, must PASS (the loop-to-cap bug).
    st = _evidence([
        ("read the TV On Bedlight Ambient triggers and actions", "it did trigger, and the one action is for the right bed light only", "bedlight_r"),
        ("press the left bedside button", "yes it comes on", "bedlight_l"),
    ])
    add("TV-BEDLIGHT ran+incomplete -> pass config", ft(_Move("conclude", "bedlight_r", "configuration_error"), st) is False)

    # device_error needs a LOCAL check on the target.
    st_nolocal = _evidence([("read Window Sensor State", "Open", "windowsensor")])
    add("device_error w/o local check -> block", ft(_Move("conclude", "windowsensor", "device_error"), st_nolocal) is True)
    st_local = {"deduction_timeline": [
        {"action": "open/close the window, watch its indicator light", "result": "no light, state didn't move",
         "action_type": "physical_check", "target_device_id": "windowsensor"}],
        "evidence": [{"source": "user", "content": "User reported (re: watch the Window Sensor light): no light, no update"}]}
    add("device_error w/ local check -> pass", ft(_Move("conclude", "windowsensor", "device_error"), st_local) is False)

    # connection_error needs BOTH halves (works locally AND system blind).
    st_conn = {"deduction_timeline": [
        {"action": "press the wall switch", "result": "light turns on", "action_type": "physical_check", "target_device_id": "doorlight"}],
        "evidence": [{"source": "user", "content": "works at the switch but the dashboard shows it unavailable"}]}
    add("connection both halves -> pass", ft(_Move("conclude", "doorlight", "connection_error"), st_conn) is False)
    # 09efba4b: plain phrasing ("not reporting" / "State does not change") looped 10 turns because
    # the system-blind regex only knew "won't update"/"unavailable". Must pass now.
    st_plain = {"deduction_timeline": [
        {"action": "open/close the door, watch the sensor's own indicator light", "result": "yes it reacts",
         "action_type": "physical_check", "target_device_id": "doorsensor"},
        {"action": "read the Door Sensor State after the door movement", "result": "it's not reporting",
         "action_type": "portal_check", "target_device_id": "doorsensor"}],
        "evidence": [{"source": "user", "content": "User reported: the State does not change"}]}
    add("connection plain phrasing (09efba4b) -> pass", ft(_Move("conclude", "doorsensor", "connection_error"), st_plain) is False)
    # Structural path: a closed-set dashboard read answered "No, still the same" is the
    # system-blind half read straight from the recorded option — no prose match needed.
    st_closed = {"deduction_timeline": [
        {"action": "open/close the door, watch the sensor's own indicator light", "result": "the light reacts",
         "action_type": "physical_check", "target_device_id": "doorsensor", "offered_options": ["Yes", "No"]},
        {"action": "After you moved the door, did the dashboard's State change?", "result": "No, still the same",
         "action_type": "portal_check", "target_device_id": "doorsensor",
         "offered_options": ["Yes, it changed", "No, still the same"]}],
        "evidence": []}
    add("connection closed-set 'No' -> pass", ft(_Move("conclude", "doorsensor", "connection_error"), st_closed) is False)
    # Same closed set but answered "Yes, it changed" -> system is NOT blind -> still blocks.
    st_yes = {"deduction_timeline": [dict(st_closed["deduction_timeline"][0]),
        {**st_closed["deduction_timeline"][1], "result": "Yes, it changed"}], "evidence": []}
    add("connection closed-set 'Yes' -> block", ft(_Move("conclude", "doorsensor", "connection_error"), st_yes) is True)
    st_half = {"deduction_timeline": [
        {"action": "press the switch", "result": "light turns on", "action_type": "physical_check", "target_device_id": "doorlight"}],
        "evidence": []}
    add("connection missing half -> block", ft(_Move("conclude", "doorlight", "connection_error"), st_half) is True)

    # heater: rule read + REAL mismatch (ABOVE 19C, opposite of expected) -> config passes.
    st_heater = _evidence([("read Temp Low Heater On conditions", "trigger every minute, condition temperature ABOVE 19C, action set heater 30C", "heater")])
    st_heater["evidence"].append({"source": "user", "content": "the condition is ABOVE 19C, that is the opposite of what I expected"})
    add("heater real-mismatch -> pass config", ft(_Move("conclude", "heater", "configuration_error"), st_heater) is False)
    return out


def _duplicate_cases():
    out = []
    dup = lambda mv, st: bool(_check_duplicate(mv, st))  # noqa: E731

    turn2 = "In All Rules, open the rule and read me its Last triggered value, just to see whether it ran recently."
    turn6 = "Staying in All Rules, read me that rule's Last triggered value again, just to see whether it ran since."
    st = {"deduction_timeline": [{"action": turn2, "result": "3d ago", "target_device_id": "smartfan"}],
          "evidence": [], "checked_tools": [], "last_check_requested": turn6}
    out.append(("3535204e re-ask -> flagged", dup(_Move("portal_check", "smartfan", reply_text=turn6), st) is True))

    door = "In All Devices, find Door Sensor and read me its State."
    window = "In All Devices, find Window Sensor and read me its State."
    st2 = {"deduction_timeline": [{"action": door, "result": "Open", "target_device_id": "doorsensor"}],
           "evidence": [], "checked_tools": [], "last_check_requested": window}
    out.append(("window-after-door (diff device) -> clean", dup(_Move("portal_check", "windowsensor", reply_text=window), st2) is False))

    # tool-derived evidence must NOT self-match (6b92ead4 spurious blocks)
    st3 = {"deduction_timeline": [], "checked_tools": [], "last_check_requested": window,
           "evidence": [{"source": "tool", "content": "Dashboard entity retrieved for Window Sensor: ."}]}
    out.append(("tool-evidence device -> not a duplicate", dup(_Move("portal_check", "windowsensor", reply_text=window), st3) is False))

    out.append(("_same_check value-phrase match", _same_check(turn2.lower(), turn6.lower()) is True))
    return out


def _groundedness_gate_cases():
    """2d363a23: a connection conclusion with BOTH discriminating halves on record was correct at
    turn 5, but the groundedness LLM rejected it three times for asserting the diagnosis as fact
    ('is' vs 'likely'), costing three redundant turns.

    Groundedness still runs on conclusions (it is the only check that catches a stray hallucinated
    value inside one), but a conclude that cleared the fault-type gate is flagged
    diagnosis_verified=True so the judge stops policing confident phrasing."""
    import smart_home_agent_backend.utils.evaluator as ev  # noqa: PLC0415

    out = []
    original = ev._check_groundedness
    seen: dict[str, object] = {}

    def _recorder(move, state, diagnosis_verified=False):
        seen[getattr(move, "action_type", "")] = diagnosis_verified
        return "ungrounded_claim: forced-for-test"

    ev._check_groundedness = _recorder
    try:
        # Both halves present: sensor works locally (physical_check) + dashboard stays blind.
        # Two participant observations in evidence so the evidence-sufficiency gate is satisfied.
        st = {"deduction_timeline": [
            {"action": "watch the sensor's own indicator light as you move the door",
             "result": "it lights", "action_type": "physical_check", "target_device_id": "doorsensor",
             "offered_options": ["Yes", "No"]},
            {"action": "did the dashboard's State change after you moved the door?",
             "result": "No, still the same", "action_type": "portal_check",
             "target_device_id": "doorsensor", "offered_options": ["Yes, it changed", "No, still the same"]}],
            "evidence": [
                {"source": "user", "content": "User reported (re: sensor indicator light): it lights"},
                {"source": "user", "content": "User reported (re: dashboard State after door move): No, still the same"}],
            "checked_tools": []}
        v_conclude = ev.evaluate_move(_Move("conclude", "doorsensor", "connection_error"), st, _CFG)
        # Still audited (a stray hallucinated value inside a conclusion must not slip through)...
        out.append(("conclude -> groundedness STILL runs",
                    "ungrounded_claim" in (v_conclude.checks_failed or [])))
        # ...but flagged as a verified diagnosis so the judge stops demanding hedging.
        out.append(("conclude -> judged with diagnosis_verified=True", seen.get("conclude") is True))

        # A non-terminal, non-duplicate check is audited normally (no verified-diagnosis clause).
        v_check = ev.evaluate_move(
            _Move("portal_check", "windowsensor", reply_text="open the Window Sensor card and read its Last changed"),
            st, _CFG)
        out.append(("portal_check -> groundedness enforced",
                    "ungrounded_claim" in (v_check.checks_failed or [])))
        out.append(("portal_check -> diagnosis_verified=False", seen.get("portal_check") is False))
    finally:
        ev._check_groundedness = original

    # The verified-diagnosis clause must actually reach the judge's prompt, naming the label.
    clause = ev._DIAGNOSIS_VERIFIED_CLAUSE.format(fault_label="connection_error")
    filled = ev._GROUNDEDNESS_PROMPT.format(evidence="- x", reply="it is a connection issue",
                                            diagnosis_clause=clause)
    out.append(("verified clause names the label + forbids hedging",
                "connection_error" in filled and "Do not demand hedging" in filled))
    # And it is absent for an ordinary check.
    plain = ev._GROUNDEDNESS_PROMPT.format(evidence="- x", reply="read the State", diagnosis_clause="")
    out.append(("no clause when not a verified diagnosis", "ALREADY VERIFIED" not in plain))

    # A judge that cannot run (no credentials / quota / timeout) must fail OPEN but stay VISIBLE:
    # the move is approved, and the turn is marked unjudged so it is not counted as audited-clean.
    down = ev._check_groundedness
    ev._check_groundedness = lambda move, state, diagnosis_verified=False: ev._GROUNDEDNESS_UNAVAILABLE
    try:
        v = ev.evaluate_move(
            _Move("portal_check", "windowsensor", reply_text="open the Window Sensor card and read Last changed"),
            st, _CFG)
        out.append(("judge down -> fails open (approved)", v.verdict == "approved"))
        out.append(("judge down -> flagged unavailable", v.groundedness_unavailable is True))
        out.append(("judge down -> not counted as a failure", not v.checks_failed))
    finally:
        ev._check_groundedness = down

    # A judge that ran clean must NOT set the flag (else every turn looks unjudged).
    ok = ev._check_groundedness
    ev._check_groundedness = lambda move, state, diagnosis_verified=False: None
    try:
        v = ev.evaluate_move(
            _Move("portal_check", "windowsensor", reply_text="open the Window Sensor card and read Last changed"),
            st, _CFG)
        out.append(("judge clean -> unavailable stays False", v.groundedness_unavailable is False))
    finally:
        ev._check_groundedness = ok
    return out


def _move_helper_cases():
    out = []
    # valid submit args build a real move
    m = _move_from_tool_args({"action_type": "physical_check", "reply_text": "press the fan buttons"})
    out.append(("submit args -> move", m.action_type == "physical_check" and "press" in m.reply_text))
    # malformed args fall back safely (never breaks the turn)
    m2 = _move_from_tool_args({"action_type": "nonsense"}, fallback_text="fallback line")
    out.append(("bad submit args -> safe fallback", m2.reply_text == "fallback line" and m2.action_type == "explain"))
    # hypotheses board: rejected peel-off + dedupe
    board = [
        HypothesisEntry(cause="fan ignores commands", discriminating_check="toggle it", status="rejected"),
        HypothesisEntry(cause="sensor not reporting", discriminating_check="re-open window", status="testing"),
    ]
    active, rejected = _merge_hypotheses({"hypotheses": [], "rejected_hypotheses": []}, board)
    out.append(("hypothesis merge active/rejected", len(active) == 1 and len(rejected) == 1))
    return out


def _reply_option_cases():
    out = []
    n = _normalize_reply_options
    out.append(("dedupe + drop Other + cap", n(["Open", "open ", "Other", "Closed.", "Not sure", "Unavailable", "x5"]) == ["Open", "Closed", "Unavailable", "x5"]))
    out.append(("single option -> none", n(["Open"]) == []))
    out.append(("empty -> none", n(None) == []))
    return out


def _model_family_cases():
    out = []
    out.append(("gpt-5.6-terra reasoning", _is_reasoning("gpt-5.6-terra") is True))
    out.append(("gpt-5.4 non-reasoning", _is_reasoning("gpt-5.4") is False))
    out.append(("gpt-5.6-mini non-reasoning", _is_reasoning("gpt-5.6-mini") is False))
    out.append(("o3 reasoning", _is_reasoning("o3") is True))
    return out


def _leak_guard_cases():
    from smart_home_agent_backend.eval.simulate import build_factsheet, _assert_no_leak, load_gt, all_scenario_ids
    out = []
    ok = True
    for sid in all_scenario_ids():
        gt = load_gt(sid)
        for mode in ("dashboard", "floor_map"):
            try:
                _assert_no_leak(build_factsheet(gt, mode), gt)
            except SystemExit:
                ok = False
    out.append(("all sim fact-sheets leak-clean", ok))
    caught = False
    try:
        _assert_no_leak("- you observe: this is clearly a device error", load_gt(all_scenario_ids()[0]))
    except SystemExit:
        caught = True
    out.append(("guard catches injected verdict", caught))
    return out


def _content_extraction_cases():
    # Responses-API messages carry a block list (reasoning + text), not a string. Session
    # 55d43900 leaked the raw repr — incl. encrypted reasoning — into the participant's reply.
    from smart_home_agent_backend.utils.state import content_to_text
    out = []
    blocks = [
        {"type": "reasoning", "encrypted_content": "gAAA_secret_cot"},
        {"type": "text", "text": "Tap Smart Fan and tell me if it responds.", "phase": "final_answer"},
    ]
    t = content_to_text(blocks)
    out.append(("extracts text from block list", "Tap Smart Fan" in t))
    out.append(("drops reasoning/encrypted", "encrypted" not in t and "gAAA" not in t and "reasoning" not in t))
    out.append(("string passthrough", content_to_text("hi") == "hi"))
    out.append(("none/empty safe", content_to_text(None) == "" and content_to_text([]) == ""))
    return out


def _scripted_participant_cases():
    from smart_home_agent_backend.eval.simulate import ScriptedParticipant, load_gt
    out = []
    p = ScriptedParticipant(load_gt("SC-DOOR-WINDOW-OPEN-FAN-OFF"))
    out.append(("opener is first-person non-empty", len(p.opening([])) > 20 and " I " in f" {p.opening([])} "))
    elicit = p.reply("What's your gut feeling about what's causing it?", []).lower()
    out.append(("declines the diagnosis question", "not sure" in elicit or "haven't tried" in elicit))
    fan = p.reply("Turn the Smart Fan off from its tile and press its own buttons — does it respond?", [])
    out.append(("fan operate -> 'responds to both'", "responds to both" in fan.lower()))
    light = p.reply("Watch the indicator light on the Window Sensor as you open and close the window.", [])
    out.append(("indicator light -> 'does not come on'", "does not come on" in light.lower()))
    fb = p.reply("What time does the dashboard clock show at the top?", [])
    out.append(("uncovered check -> neutral fallback", fb.startswith("It looks the same")))
    # oracle can only ever return truthful observations (never a diagnosis) — leak-safe by build
    out.append(("never emits a verdict word", all(
        v not in p.reply(q, []).lower()
        for q in ("check the fan", "read the rule", "look at the sensor")
        for v in ("device error", "faulty", "misconfigured"))))
    return out


def main() -> int:
    groups = [
        ("fault-type gate", _fault_type_cases),
        ("groundedness gate ordering", _groundedness_gate_cases),
        ("duplicate check", _duplicate_cases),
        ("move helpers / single-call", _move_helper_cases),
        ("reply options", _reply_option_cases),
        ("model-family detection", _model_family_cases),
        ("sim-user leak guard", _leak_guard_cases),
        ("content extraction (Responses API)", _content_extraction_cases),
        ("scripted participant", _scripted_participant_cases),
    ]
    print("\n══ Logic checks (offline, free) ═══════════════════════════════")
    total, failed = 0, 0
    for gname, fn in groups:
        print(f"\n── {gname}")
        try:
            cases = fn()
        except Exception as exc:  # a broken case file is itself a failure
            print(f"   ERROR building cases: {exc}")
            failed += 1
            continue
        for name, ok in cases:
            total += 1
            if not ok:
                failed += 1
            print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n══ {total - failed}/{total} passed" + ("" if not failed else f"  — {failed} FAILED") + "\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
