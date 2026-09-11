"""The evaluator: audit the agent's next move BEFORE the participant sees it.

Not a supervisor routing between specialists — there are no specialists. One domain, and a
second, cheaper pass asking four questions of the move solve_node just produced:

    is it a duplicate?          -> a rule. Nobody needs a model to notice a repeat.
    is the action possible?     -> a lookup. The curtain has no button; the sensors have no toggle.
    is there enough evidence?   -> a rule.
    is the claim grounded?      -> the ONE question that needs judgment. The hallucination catch.

Checks run cheapest-first and short-circuit, so the model call only happens when the rule
checks have already passed.

VERDICTS
    approved -> ship it
    revise   -> send it back to solve_node once, with feedback naming what was wrong
    block    -> a second failure. Emit a safe fallback rather than loop; a participant sitting
                in the lab cannot be left waiting while the agent argues with itself.

EVERY verdict is logged, including approvals, with the checks that failed. That log is the
thesis' hallucination / duplicate / impossible-action count — measured, not estimated.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel

from smart_home_agent_backend.utils.config_loader import find_device
from smart_home_agent_backend.utils.llm import chat_model as init_chat_model

# The groundedness check is the only one that needs a model, and it is a narrow judgment
# ("is this claim supported?"), not a reasoning task. A smaller model is enough and keeps the
# latency off every turn the participant waits through. Override if the timings say otherwise.
EVALUATOR_MODEL = os.getenv("SMART_HOME_EVALUATOR_MODEL", os.getenv("SMART_HOME_MODEL", "gpt-5.6-terra"))

MAX_REVISIONS = 1


class EvaluatorVerdict(BaseModel):
    verdict: Literal["approved", "revise", "block"]
    feedback: Optional[str] = None
    checks_failed: list[str] = []
    # True when the groundedness judge could NOT run (no credentials, quota exhausted, timeout).
    # The move is still let through — a dead judge must never block a live participant — but an
    # "approved" carrying this flag means NEVER JUDGED, not "judged and clean". Without it the
    # two are indistinguishable in the log, and the evaluator log is a measurement instrument.
    groundedness_unavailable: bool = False


class _Groundedness(BaseModel):
    """The single LLM judgment. Deliberately narrow."""
    grounded: bool
    unsupported_claim: Optional[str] = None


# Returned by _check_groundedness when the judge itself could not run. Distinct from None
# ("judged, clean") and from a failure string ("judged, flagged") — the third outcome that
# used to be invisible.
_GROUNDEDNESS_UNAVAILABLE = "__groundedness_unavailable__"


# ── The safe fallback, for `block` ────────────────────────────────────────────────────
#
# A participant sees this live, mid-session. It must sound like the agent having a think —
# not like an error. It must not invent a new claim (that is what got us here), and it must
# still move the session forward, so it hands control back to the participant.
BLOCK_FALLBACK = (
    "Let me take stock for a second. Rather than guess, tell me what stands out to you "
    "most right now — and we'll check that."
)


# ── Check 1: duplicates (rule) ────────────────────────────────────────────────────────

# Words that appear in nearly every check instruction and carry no content. Without
# removing them, ANY two polite portal instructions look alike; with them removed, what's
# left is the substance ("rules", "last", "triggered", "value") that actually identifies
# a check.
_CHECK_STOPWORDS = frozenset(
    "the a an and or to me its it's just so we can see whether that this on in of for "
    "tell read find open go stay staying now please your you what did does with from "
    "one once again too also then there here right okay ok good nice perfect thanks "
    "thank got let lets dashboard".split()
)


def _content_tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _CHECK_STOPWORDS
    }


# Filler that can wrap the requested value without changing WHAT is requested:
# "read me that rule's Last triggered value again" asks for the same thing as
# "read me its Last triggered value".
_VALUE_FILLER = frozenset(
    "its the that this their a an rule rule's rules rules' exact exactly again "
    "too once more please".split()
)


def _requested_value(text: str) -> str:
    """The value a check asks the participant to report ("last triggered value",
    "state", "actions line"), normalized. Empty when no read-me/tell-me pattern
    is present (e.g. physical checks phrased as "what do you see?")."""
    m = re.search(r"(?:read|tell|give)\s+me\s+([^,.!?;:]{3,80})", text, re.IGNORECASE)
    if not m:
        return ""
    words = [
        w for w in re.findall(r"[a-z']+", m.group(1).lower()) if w not in _VALUE_FILLER
    ]
    return " ".join(words[:5])


def _same_check(a: str, b: str) -> bool:
    """Are these two instructions asking for the same thing?

    Whole-sentence SequenceMatcher misses re-asks wrapped in fresh pleasantries
    (session 3535204e: the second "read me its Last triggered value" scored below
    threshold purely because its opener and tail differed). The checks in this
    domain are template-shaped, so the requested VALUE phrase is the reliable
    identity: same value on the same device = the same check, however it is
    dressed up. Falls back to text similarity when no value can be extracted.
    """
    va, vb = _requested_value(a), _requested_value(b)
    if va and vb:
        return va == vb
    if _similar(a, b):
        return True
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return False
    # Containment (overlap over the smaller side), not Jaccard: a re-ask padded
    # with acknowledgment words must not escape by making the union bigger.
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.75


def _check_duplicate(move: Any, state: dict[str, Any]) -> Optional[str]:
    """Has this exact check already been asked, with nothing new since?

    The single most common complaint in the pilot sessions: "you could've known it at the
    first place when I told you". Re-asking is not a small annoyance — it is noise injected
    straight into the efficiency measure the study exists to take.
    """
    action_type = getattr(move, "action_type", "")
    if action_type not in {"physical_check", "portal_check"}:
        return None

    target = (getattr(move, "target_device_id", "") or "").strip().lower()
    reply = " ".join((getattr(move, "reply_text", "") or "").lower().split())

    # The same check already asked AND answered, anywhere in the session — not just the
    # immediately preceding turn. The old code compared only against last_check_requested,
    # so a check re-asked with anything in between sailed through (session 3535204e:
    # "Last triggered" asked at turn 2, re-asked at turn 6 -> "noooooo..."). A matching
    # device gate keeps near-identical instructions on DIFFERENT devices (door State /
    # window State) from being false positives.
    for step in state.get("deduction_timeline", []) or []:
        if not isinstance(step, dict) or not str(step.get("result", "")).strip():
            continue
        step_target = str(step.get("target_device_id", "") or "").strip().lower()
        if target and step_target and target != step_target:
            continue
        action = " ".join(str(step.get("action", "")).lower().split())
        if action and _same_check(reply, action):
            return (
                f"duplicate_check: you already asked this and the participant answered: "
                f"\"{str(step.get('result', ''))[:120]}\". Re-asking tells them you were not "
                f"listening. If something has genuinely changed and you need it re-checked, "
                f"acknowledge their earlier answer and say WHY once more — otherwise make a "
                f"different move."
            )

    # Checks the participant has already answered, as recorded in evidence ("User
    # reported (re: <instruction>): <answer>"). Compare the NEW reply against that
    # embedded INSTRUCTION — never against state.last_check_requested, which solve_node
    # has already overwritten with the current reply by the time this audit runs, so
    # comparing to it is comparing the reply to ITSELF (session 6b92ead4: every check on
    # any device with tool-derived evidence self-matched and got blocked). Skip non-user
    # evidence for the same reason: the agent's own tool lookups are not answers.
    squashed_target = re.sub(r"[^a-z0-9]", "", target)
    for item in state.get("evidence", []) or []:
        if str(item.get("source", "")) != "user":
            continue
        content = str(item.get("content", ""))
        if squashed_target and squashed_target not in re.sub(r"[^a-z0-9]", "", content.lower()):
            continue
        answered = re.match(r"\s*user reported \(re:\s*(.+?)\):", content.lower(), re.DOTALL)
        if answered and _same_check(reply, " ".join(answered.group(1).split())):
            return (
                f"duplicate_check: you have already asked the participant to do this, and "
                f"they answered. Re-asking wastes their time and tells them you were not "
                f"listening. What they reported: \"{content[:120]}\""
            )

    for record in state.get("checked_tools", []) or []:
        if not isinstance(record, dict):
            continue
        args = str(record.get("tool_args", "")).lower()
        if target and target in args and action_type == "portal_check":
            result = str(record.get("tool_result", ""))[:120]
            if _similar(reply, str(record.get("tool_name", ""))):
                return f"duplicate_check: already established for {target}: {result}"

    return None


def _similar(a: str, b: str, threshold: float = 0.72) -> bool:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= threshold


# ── Check 2: affordances (rule) ───────────────────────────────────────────────────────
#
# READS environment.devices[*].controls.physical — NOT device_knowledge.safe_user_checks,
# which was empty for all 16 devices and has been deleted. A veto built on empty lists vetoes
# nothing, and would have missed the exact bug it was written for: a pilot session where the
# agent told a participant to press the curtain's physical button, and the participant replied
# "Doesn't have a physical button."

_PHYSICAL_VERBS = re.compile(
    r"\b(press|push|flip|toggle|switch|turn)\b[^.?!]{0,40}\b"
    r"(button|switch|dial|knob|by hand|manually|on the (device|unit|fan|radiator))\b",
    re.IGNORECASE,
)


def _check_affordance(move: Any, config: dict[str, Any]) -> Optional[str]:
    if getattr(move, "action_type", "") != "physical_check":
        return None

    reply = getattr(move, "reply_text", "") or ""
    if not _PHYSICAL_VERBS.search(reply):
        return None  # not proposing a hands-on control at all

    # Judge the device the HANDS go on, not the device under test. These differ on every
    # bypass check ("unplug the fan from its plug and press the fan's own button": target =
    # the plug, operated = the fan), and reading the fan's button as the plug's is what made
    # this check veto three correct moves in session 13fa79f9. Fall back to target when the
    # model names no operated device: that is the old behaviour, and it errs toward vetoing.
    operated = getattr(move, "operated_device_id", "") or ""
    target = getattr(move, "target_device_id", "") or ""
    handled = operated or target
    device = find_device(config["environment"], handled) if handled else None
    if not device:
        return None  # cannot judge; do not veto on a guess

    controls = (device.get("controls") or {}).get("physical") or []
    name = device.get("name", target)

    if not controls:
        return (
            f"impossible_action: the {name} has NO physical control. There is nothing on it to "
            f"press, flip or turn. Asking the participant to operate it by hand sends them to "
            f"look for something that does not exist."
        )

    unusable = [c for c in controls if c.get("usable") is False]
    if unusable and len(unusable) == len(controls):
        reason = " ".join(str(unusable[0].get("note", "")).split())
        return (
            f"forbidden_action: the {name}'s only physical control must NEVER be used. {reason} "
            f"Propose something else."
        )

    return None


# ── Check 3: evidence sufficiency before concluding (rule) ────────────────────────────

def _check_evidence_sufficiency(move: Any, state: dict[str, Any]) -> Optional[str]:
    """Enough to conclude?

    THE PARTICIPANT'S REPORT COUNTS. They are looking at the dashboard and at the room; the
    agent is not. A value they read back is the best evidence in the building, and requiring
    "tool-verified" facts would have blocked a pilot session that concluded correctly from two
    participant reports.

    So the bar is: at least TWO distinct observations, from any source.
    """
    if getattr(move, "action_type", "") not in {"conclude", "escalate"}:
        return None

    reported = [
        e for e in (state.get("evidence", []) or [])
        if isinstance(e, dict) and str(e.get("content", "")).strip()
    ]
    facts = state.get("confirmed_facts", []) or []
    observations = len(reported) + len(facts)

    if observations < 2:
        return (
            f"insufficient_evidence: you are concluding on {observations} observation(s). Get at "
            f"least two distinct ones first — a device operated and watched, a value read back, a "
            f"rule opened. One data point is a guess with a confident voice."
        )
    return None


# ── Check 4: the causal chain (rule) ──────────────────────────────────────────────────

_DOWNSTREAM_OF: dict[str, str] = {
    # device -> the thing UPSTREAM of it that can explain its silence
    "smartfan": "socket_fan",   # the fan draws its mains power through the smart plug
}


def _check_upstream_not_ruled_out(move: Any, state: dict[str, Any], config: dict[str, Any]) -> Optional[str]:
    """Do not blame a casualty.

    A device that is dead because its power was cut is not the fault; the thing that cut the
    power is. This is exactly the fan/plug case: the fan is unavailable and responds to
    nothing, which looks precisely like a broken fan — and the fan is perfectly healthy.

    So before concluding a DEVICE fault on something that sits downstream of another
    component, that component must have been looked at.
    """
    if getattr(move, "action_type", "") not in {"conclude", "escalate"}:
        return None
    if getattr(move, "fault_label", None) != "device_error":
        return None

    target = (getattr(move, "target_device_id", "") or "").strip()
    upstream_id = _DOWNSTREAM_OF.get(target)
    if not upstream_id:
        return None

    seen = " ".join(
        [str(e.get("content", "")) for e in (state.get("evidence", []) or []) if isinstance(e, dict)]
        + [str(d) for d in (state.get("checked_devices", []) or [])]
    ).lower()

    upstream = find_device(config["environment"], upstream_id)
    names = [upstream_id] + ([upstream.get("name", "")] if upstream else [])
    if any(n and n.lower() in seen for n in names):
        return None  # they have looked at it

    up_name = (upstream or {}).get("name", upstream_id)
    tgt_name = (find_device(config["environment"], target) or {}).get("name", target)
    return (
        f"unchecked_upstream: you are about to call the {tgt_name} a device fault, but it is "
        f"powered through the {up_name}, and nobody has checked that. A device with no power "
        f"behaves EXACTLY like a dead one. Establish that the {up_name} is actually supplying "
        f"power before blaming the {tgt_name}."
    )


# ── Check 5: fault type is resolved, not guessed (rule) ───────────────────────────────
#
# Localizing the faulty component is only half a conclusion; the study also grades WHICH of
# device / connection / configuration it is, and each has one discriminating check. Session
# 4abbad5c concluded "configuration_error" after the participant read the rule and it MATCHED
# their expectation — a matching rule is not a misconfiguration, and the real fault (the
# window sensor not emitting events) was a device fault that was never locally checked. This
# gate refuses a fault_label whose discriminating evidence is not present.

# The rule's contents were actually read off its card (not just its name/Last-triggered).
_RULE_READ_RE = re.compile(r"\b(trigger|condition)s?\b", re.IGNORECASE)
# The participant/agent established the rule MATCHES what was expected.
_RULE_MATCH_RE = re.compile(
    r"\b(match(es|ed)?)\b[^.?!]{0,30}\b(expect|what you|your)\b"
    r"|\bas (you )?expected\b|\bwhat you expected\b",
    re.IGNORECASE,
)
# An explicit MISMATCH between rule text and expectation (so configuration IS in play). Includes
# PARTIAL-action faults — a rule that does some of what was expected but not all (commands only
# one of two lights, sets only one device) is a configuration fault, not a match. Session
# ab_full_optimized/TV-BEDLIGHT looped because "only commands Bedlight Right" wasn't caught here.
_RULE_MISMATCH_RE = re.compile(
    r"\b(does\s*n'?t|do\s*not|does not|doesn'?t)\s+match\b"
    r"|\bnot what (you|they) expected\b|\binstead of\b|\bopposite\b"
    r"|\bshould (have )?(be|been|also)\b|\bmismatch\b"
    r"|\bonly (commands?|controls?|turns?|sets?|touch(es)?|affects?|does)\b"
    r"|\bmissing\b|\bnot both\b|\bjust the\b|\bleaves\b.{0,20}\b(off|out)\b|\bstays off\b",
    re.IGNORECASE,
)

# Evidence the rule did NOT fire — the other half of the 4abbad5c trap. Only when a rule both
# MATCHES expectation AND never ran is the fault upstream (device/connection) rather than the
# rule. Matches "3d ago" / "days ago" / "never triggered" / "no recent activity", NOT a fresh
# "10 minutes ago". A rule that RAN but produced the wrong result is a genuine config fault.
_RULE_STALE_RE = re.compile(
    r"\bnever (ran|triggered|fired)\b"
    r"|\b(has\s*n'?t|have\s*n'?t|did\s*n'?t|does\s*n'?t)\s+(trigger(ed)?|run|ran|fire[d]?)\b"
    r"|\bnot (yet )?(triggered|fired|run|ran)\b|\bno recent activity\b"
    r"|\b\d+\s*(d|day|days|w|week|weeks|mo|month|months|y|year|years)\s*ago\b"
    r"|\b(days?|weeks?|months?|years?)\s+ago\b|\bstuck\b",
    re.IGNORECASE,
)
# Operating a device at its OWN local control, or making its own state change by hand.
_LOCAL_OP_RE = re.compile(
    r"\b(press|push|toggl|flip|switch|button|indicator|by hand|manually"
    r"|open(ed)? and clos|clos(ed)? and open|re-?open|trip|its (own )?light|operate)\b",
    re.IGNORECASE,
)
# The system cannot see the device even though it demonstrably changed. Broadened after session
# 09efba4b looped for 10 turns: the participant said "it's not reporting" and "the State does not
# change" — plainer phrasings than the original regex ("won't update"/"unavailable") caught — so
# the gate never saw the system-blind half and blocked a CORRECT connection conclusion forever.
# Now matches negated update/report/change/move/reach/register/refresh/receive verbs generally.
_SYSTEM_BLIND_RE = re.compile(
    r"\bunavailable\b|\boffline\b|\bnot reachable\b|\bcan'?t see\b|\bstuck\b"
    r"|\bstill (shows|says|reads|open|closed)\b"
    r"|\bno (recent )?(update|light|response|change|activity)\b"
    r"|\b(does\s*n'?t|do\s*not|is\s*n'?t|are\s*n'?t|was\s*n'?t|were\s*n'?t|wo\s*n'?t|will not"
    r"|did\s*n'?t|has\s*n'?t|have\s*n'?t|not|never)\s+"
    r"(yet\s+|ever\s+)?(updat\w*|report\w*|chang\w*|mov\w*|reach\w*|register\w*|refresh\w*|receiv\w*)",
    re.IGNORECASE,
)


# A closed-set "did the system reflect the change?" check is about the dashboard/portal/app
# reading, not the physical device. Used to recognise the structured system-blind answer.
_UPDATE_TOPIC_RE = re.compile(
    r"dashboard|portal|\bapp\b|\bui\b|\bstate\b|status|reading|reflect|update|sync|register|report",
    re.IGNORECASE,
)
# The negative pick on that closed set ("No", "Didn't change", "Still open", "Unchanged").
_NEG_UPDATE_RE = re.compile(
    r"\bno\b|\bnope\b|\bdid\s*n'?t\b|\bdoes\s*n'?t\b|\bunchanged\b|\bstayed\b|\bsame\b"
    r"|\bstill\b|\bnot\b|\bnever\b|\bblank\b|\bmissing\b",
    re.IGNORECASE,
)
_POS_UPDATE_RE = re.compile(
    r"\byes\b|\bupdated\b|\bchanged\b|\breflect|\bin sync\b|\bsynced\b|\bcorrect\b|\bmatch",
    re.IGNORECASE,
)


def _structured_system_blind(state: dict[str, Any]) -> bool:
    """Did the participant answer a CLOSED-SET dashboard/system-read check with the negative
    option? That is an unambiguous system-blind signal read straight from the recorded answer —
    no free-text inference. Added after session 09efba4b looped because the prose regex missed
    'it's not reporting' / 'the State does not change'. A closed set makes the answer canonical."""
    for step in state.get("deduction_timeline", []) or []:
        if not isinstance(step, dict):
            continue
        options = step.get("offered_options") or []
        result = str(step.get("result", "")).strip()
        if not options or not result:
            continue
        topic = f"{step.get('action','')} {' '.join(str(o) for o in options)}"
        if not _UPDATE_TOPIC_RE.search(topic):
            continue
        if _NEG_UPDATE_RE.search(result) and not _POS_UPDATE_RE.search(result):
            return True
    return False


def _discriminator_hint(device: dict[str, Any] | None) -> str:
    """The concrete local check that separates a device fault from a connection fault, phrased
    for THIS device's kind. For a sensor that is its own indicator light; for anything the
    participant can operate, its own buttons/switch. Naming it turns a vague 'localize the type'
    into a move the agent can actually make on the retry."""
    dtype = str((device or {}).get("type", "")).lower()
    name = (device or {}).get("name", "the device")
    if dtype == "sensor":
        return (
            f"have the participant make {name} trip by hand (open/close the window, the door, "
            f"the contact) while they watch the small indicator light ON the sensor itself — "
            f"does that light respond, yes or no"
        )
    return (
        f"have the participant operate {name} at its OWN control — its buttons or switch on the "
        f"unit — and watch whether it physically acts"
    )


def _target_names(config: dict[str, Any], target: str) -> list[str]:
    names = {target.lower()} if target else set()
    device = find_device(config.get("environment", {}), target) if target else None
    if device:
        names.add(str(device.get("name", "")).lower())
        for a in device.get("aliases", []) or []:
            names.add(str(a).lower())
    return [n for n in names if n]


def _has_local_evidence(state: dict[str, Any], target: str, names: list[str]) -> bool:
    """Did the participant operate this device at its own control, or make its own
    state change by hand, and report the result?"""
    for step in state.get("deduction_timeline", []) or []:
        if not isinstance(step, dict):
            continue
        if (
            str(step.get("target_device_id", "")).lower() == target
            and step.get("action_type") == "physical_check"
            and str(step.get("result", "")).strip()
        ):
            return True
    texts = [str(e.get("content", "")) for e in (state.get("evidence", []) or []) if isinstance(e, dict)]
    texts += [
        f"{s.get('action','')} {s.get('result','')}"
        for s in (state.get("deduction_timeline", []) or []) if isinstance(s, dict)
    ]
    for t in texts:
        tl = t.lower()
        if _LOCAL_OP_RE.search(tl) and any(n in tl for n in names):
            return True
    return False


def _corpus(state: dict[str, Any]) -> str:
    parts = [str(e.get("content", "")) for e in (state.get("evidence", []) or []) if isinstance(e, dict)]
    for s in state.get("deduction_timeline", []) or []:
        if isinstance(s, dict):
            parts.append(f"{s.get('action','')} {s.get('result','')}")
    return " ".join(parts).lower()


def _check_fault_type(move: Any, state: dict[str, Any], config: dict[str, Any]) -> Optional[str]:
    if getattr(move, "action_type", "") not in {"conclude", "escalate"}:
        return None
    label = getattr(move, "fault_label", None)
    if not label:
        return None

    target = (getattr(move, "target_device_id", "") or "").strip().lower()
    names = _target_names(config, target)
    corpus = _corpus(state)

    if label == "configuration_error":
        if not _RULE_READ_RE.search(corpus):
            return (
                "fault_type_unresolved: to call this a configuration fault, have the participant "
                "read the rule's Conditions and Actions off its card and show they do NOT match "
                "what they expected. You have not established the rule's contents."
            )
        # Block ONLY the 4abbad5c trap: the rule fully MATCHES expectation AND never fired
        # (stale / no recent activity). That specific combination means the trigger event never
        # arrived, so the fault is the DEVICE upstream, not the rule. A rule that RAN but did the
        # wrong or INCOMPLETE thing (commands only one of two lights) is a real config fault —
        # do not block it (that was the TV-BEDLIGHT loop). If we can't establish the rule is
        # stale, trust the agent's config conclusion.
        if (
            _RULE_MATCH_RE.search(corpus)
            and not _RULE_MISMATCH_RE.search(corpus)
            and _RULE_STALE_RE.search(corpus)
        ):
            device = find_device(config.get("environment", {}), target)
            return (
                "fault_type_mismatch: DO NOT CONCLUDE this turn. The rule MATCHES what the "
                "participant expected AND has not fired (stale / no recent activity) — a correct "
                "rule that never ran means its trigger event never arrived, so the fault is the "
                f"DEVICE that should produce it. Next move: {_discriminator_hint(device)}."
            )
        return None

    device = find_device(config.get("environment", {}), target)

    if label == "device_error":
        if not _has_local_evidence(state, target, names):
            return (
                f"fault_type_unresolved: DO NOT CONCLUDE this turn — you have not run the check "
                f"that separates a device fault from a connection fault. Next move: "
                f"{_discriminator_hint(device)}. If that local signal is dead too it is a device "
                f"fault; if it responds while the dashboard stays stale it is a connection fault."
            )
        return None

    if label == "connection_error":
        # System-blind half: prefer the STRUCTURED answer (a negative pick on a closed-set
        # dashboard read); fall back to prose only when no closed set was used.
        system_blind = _structured_system_blind(state) or bool(_SYSTEM_BLIND_RE.search(corpus))
        if not (_has_local_evidence(state, target, names) and system_blind):
            return (
                f"fault_type_unresolved: DO NOT CONCLUDE this turn — a connection fault needs BOTH "
                f"halves on record: the device visibly works locally AND the system cannot see it. "
                f"Confirm the second half with a closed-set read — ask whether the dashboard/state "
                f"changed after the local action (Yes / No), then compare. "
                f"Next move: {_discriminator_hint(device)}."
            )
        return None

    return None


# ── Check 6: groundedness (LLM) ───────────────────────────────────────────────────────

_GROUNDEDNESS_PROMPT = """You are auditing one message a smart-home troubleshooting assistant is about to send.

Your ONLY job: does the message assert anything about the state of a device, a sensor, an
automation, OR its physical location that is NOT supported by the evidence below?

EVIDENCE THE ASSISTANT ACTUALLY HAS:
{evidence}

THE MESSAGE:
{reply}
{diagnosis_clause}
Rules for your judgment:
- Asking the participant to check something is NOT a claim. Questions are always grounded.
- Proposing a hypothesis IS allowed, if it is phrased as one ("it might be...", "let's see if...").
- What is NOT allowed is stating a device's state, a rule's behaviour, or a value as fact when
  nothing in the evidence establishes it. That is the hallucination we are catching.
- A device's PHYSICAL POSITION is also a factual claim: which side it is on (left/right), which
  window/wall it is by, that something is "behind" or "above" or "next to" another thing. Flag
  these too when the evidence (a location_hint from a tool result, or something the participant
  said) does not support them. Telling a participant to look on "the right side" when nothing
  established the side sends them to the wrong place — the same defect as a wrong state value.
- General knowledge about how the home is built (protocols, what a device is) is fine.

Set grounded=false ONLY for an unsupported factual assertion, and quote it."""


# Appended when the move is a conclusion that has ALREADY cleared the fault-type gate. Without
# it, the judge re-litigated verified diagnoses and demanded hedging: session 2d363a23 lost three
# turns because "so this IS a connection issue" was rejected while the identical finding phrased
# "so its connection is the LIKELY issue" passed. Confidence is not a hallucination.
_DIAGNOSIS_VERIFIED_CLAUSE = """
THE DIAGNOSIS IN THIS MESSAGE IS ALREADY VERIFIED:
This message concludes a {fault_label}. A separate, stricter check has ALREADY confirmed that the
discriminating evidence for that exact classification is on record. So the diagnosis itself IS
grounded: stating it plainly as a finding ("this is a connection issue", "the sensor is faulty")
is CORRECT here and must NOT be flagged. Do not demand hedging — "likely", "might be", "possibly"
are not required, and asking for them is a false positive.
Judge ONLY the message's OTHER assertions: specific values, timestamps, a rule's written
behaviour, or claims about devices that were never actually checked.
"""


def _check_groundedness(
    move: Any, state: dict[str, Any], diagnosis_verified: bool = False
) -> Optional[str]:
    """Audit the outgoing message for unsupported factual assertions.

    `diagnosis_verified` is set for a conclude/escalate that has already cleared the fault-type
    gate. The judge is then told the classification itself is established, so it audits the
    message's other claims instead of policing how confidently the verdict is worded.
    """
    reply = (getattr(move, "reply_text", "") or "").strip()
    if not reply:
        return None

    evidence_lines = []
    for e in (state.get("evidence", []) or [])[-12:]:
        if isinstance(e, dict) and e.get("content"):
            evidence_lines.append(f"- {e['content']}")
    for f in (state.get("confirmed_facts", []) or [])[-12:]:
        if isinstance(f, dict) and f.get("content"):
            evidence_lines.append(f"- {f['content']}")
    for t in (state.get("checked_tools", []) or [])[-8:]:
        if isinstance(t, dict):
            evidence_lines.append(f"- tool {t.get('tool_name')} -> {str(t.get('tool_result'))[:160]}")

    evidence = "\n".join(evidence_lines) or "- (nothing established yet this session)"

    diagnosis_clause = ""
    if diagnosis_verified:
        diagnosis_clause = _DIAGNOSIS_VERIFIED_CLAUSE.format(
            fault_label=getattr(move, "fault_label", None) or "fault"
        )

    try:
        judge = init_chat_model(EVALUATOR_MODEL, temperature=0).with_structured_output(_Groundedness)
        result = judge.invoke([{
            "role": "user",
            "content": _GROUNDEDNESS_PROMPT.format(
                evidence=evidence, reply=reply, diagnosis_clause=diagnosis_clause
            ),
        }])
    except Exception:
        # A judge that cannot run must never block the session. Fail open — but say so, rather
        # than returning the same None that means "judged and clean". Callers surface this as
        # groundedness_unavailable so an unjudged turn is visible in the log.
        return _GROUNDEDNESS_UNAVAILABLE

    if isinstance(result, _Groundedness) and not result.grounded:
        claim = (result.unsupported_claim or "").strip()
        return (
            f"ungrounded_claim: you stated something as fact that nothing has established: "
            f"\"{claim}\". Either check it, or say it as a possibility rather than a finding."
        )
    return None


# ── The node ──────────────────────────────────────────────────────────────────────────

def evaluate_move(move: Any, state: dict[str, Any], config: dict[str, Any]) -> EvaluatorVerdict:
    """Run the checks, cheapest first. Short-circuits: the model call only happens if the
    rule checks all pass."""
    failures: list[str] = []
    groundedness_unavailable = False

    for check in (
        lambda: _check_duplicate(move, state),
        lambda: _check_affordance(move, config),
        lambda: _check_evidence_sufficiency(move, state),
        lambda: _check_upstream_not_ruled_out(move, state, config),
        lambda: _check_fault_type(move, state, config),
    ):
        problem = check()
        if problem:
            failures.append(problem)
            break  # one clear reason is more actionable than four

    if not failures:
        # Groundedness runs on EVERY move, conclusions included — it is the only check that can
        # catch a stray hallucinated value inside an otherwise-valid conclusion. But a
        # conclude/escalate reaching this point has already cleared the fault-type gate, so its
        # classification is established; tell the judge that, or it re-litigates the verdict and
        # demands hedging (session 2d363a23 lost three turns to "is" -> "likely").
        diagnosis_verified = getattr(move, "action_type", "") in {"conclude", "escalate"}
        problem = _check_groundedness(move, state, diagnosis_verified=diagnosis_verified)
        if problem == _GROUNDEDNESS_UNAVAILABLE:
            # The judge never ran. Let the move through, but mark the turn as unjudged so the
            # log distinguishes it from a turn that was audited and came back clean.
            groundedness_unavailable = True
        elif problem:
            failures.append(problem)

    if not failures:
        return EvaluatorVerdict(
            verdict="approved",
            checks_failed=[],
            groundedness_unavailable=groundedness_unavailable,
        )

    already_revised = int(state.get("revision_count", 0) or 0)
    verdict = "revise" if already_revised < MAX_REVISIONS else "block"

    return EvaluatorVerdict(
        verdict=verdict,
        feedback=failures[0],
        checks_failed=[f.split(":", 1)[0] for f in failures],
        groundedness_unavailable=groundedness_unavailable,
    )
