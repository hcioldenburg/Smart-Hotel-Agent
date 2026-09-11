from __future__ import annotations

"""
Graph nodes for the smart-home troubleshooting agent.

Visual outputs are stored in state["visual_outputs"] and returned by the API.
The assistant should not paste raw image paths into the reply text.
"""

import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Literal, Optional

from langchain_core.messages import AIMessage, RemoveMessage
from pydantic import BaseModel, Field

from smart_home_agent_backend.utils.config_loader import (
    build_compiled_config,
    build_runtime_system_prompt,
    find_device,
    find_device_candidates,
    list_environment_devices,
)
from smart_home_agent_backend.utils.llm import chat_model as init_chat_model
from smart_home_agent_backend.utils.evaluator import (
    BLOCK_FALLBACK,
    evaluate_move,
)
from smart_home_agent_backend.utils.state import content_to_text
from smart_home_agent_backend.utils.tools import (
    get_space_info,
    list_devices,
    get_device_info,
    get_device_knowledge,
    check_hub_status,
    check_device_connectivity,
    show_device_location,
    show_hub_location,
    get_portal_overview,
    list_portal_tasks,
    get_portal_task_guide,
    get_portal_entity_info,
    get_device_portal_check_guide,
)


TOOLS = [
    get_space_info,
    list_devices,
    get_device_info,
    get_device_knowledge,
    check_hub_status,
    check_device_connectivity,
    show_device_location,
    show_hub_location,
    get_portal_overview,
    list_portal_tasks,
    get_portal_task_guide,
    get_portal_entity_info,
    get_device_portal_check_guide,
]

MODEL_NAME = os.getenv("SMART_HOME_MODEL", "gpt-5.6-terra")


class HypothesisEntry(BaseModel):
    """One candidate cause on the ranked diagnostic board (Phase 10).

    The board is the agent's plan made explicit: every plausible cause of the
    reported symptom, each paired with the single cheapest check that would
    confirm or reject it, ranked so rank 1 is the fastest-settling check. It
    persists across turns (utils/state.HypothesisItem), so a rejected branch is
    never silently re-walked and the agent always moves to the next-best test.
    """

    cause: str                       # one short falsifiable phrase — the WHY, not the symptom
    discriminating_check: str        # the single cheapest check that settles this cause
    status: Literal["untested", "testing", "supported", "rejected"] = "untested"
    device_id: Optional[str] = None  # the device/rule this cause concerns, if one applies


class DiagnosticMove(BaseModel):
    """One structured diagnostic move (Phase 2), emitted once the model has no
    more information-gathering tools to call and is ready to reply to the user.

    Replaces regex classification of freeform text: the move's fields drive
    state deterministically and give the Phase 8 evaluator something structured
    to audit.
    """

    action_type: Literal[
        "ask_clarification", "physical_check", "portal_check",
        "explain", "conclude", "escalate",
    ]
    target_device_id: Optional[str] = None

    # The device the participant is told to PUT THEIR HANDS ON this turn, which is not
    # always the device under test. On a bypass check — "unplug the fan from its smart
    # plug and press the fan's own button" — the target is the plug, but the thing
    # operated is the fan. The affordance check needs both: keyed on target alone it read
    # "the fan's own button" as a button on the PLUG, vetoed a correct instruction three
    # times in session 13fa79f9, and then approved the same wording on the turns where the
    # model happened to name the fan as target — the same move, a coin-flip verdict.
    # Leave empty when the reply asks for no hands-on operation at all.
    operated_device_id: Optional[str] = None

    # `rationale` is the RESEARCH LOG's why — it is not shown to anyone. It used to be the
    # only place a reason existed, and that was the bug: the model dutifully wrote "why this
    # check matters" here, and left reply_text as a bare imperative ("press the buttons on
    # the Smart Fan"). No prompt rule could fix that, because the model was not disobeying —
    # it was answering the question the schema asked. The reason has to be IN the message
    # the participant reads, so reply_text now owns it and this field just records it.
    rationale: str = ""
    reply_text: str              # the actual message shown to the user — MUST carry the purpose

    # The agent's CURRENT MOST PROBABLE CAUSE, as one short falsifiable phrase — "a rule is not
    # firing", "the window sensor is not reporting", "the plug is not supplying power". Updated
    # every turn as evidence lands; empty only while there is genuinely no hypothesis yet.
    #
    # This is NOT the participant's suspect. primary_suspect_* records THEIR mental model (a
    # research variable, kept verbatim); this field is the AGENT's inference — the thing each
    # next check is trying to reject or confirm. The case file used to show the participant's
    # raw first message as "prime suspect", which displayed an echo where a diagnosis belonged.
    working_hypothesis: str = ""
    requested_value: Optional[str] = None    # what the user should report back

    # Quick-reply buttons for THIS check, emitted only when the expected answer comes
    # from a small closed set the participant just has to read off ("Open" / "Closed" /
    # "Not listed"). Left empty for open-ended questions — a button row under "what's
    # your gut feeling?" would be meaningless. "Other" must never be listed here: the
    # UI appends its own "Other…" chip so the participant can always type freely.
    reply_options: list[str] = Field(default_factory=list)
    offers_free_choice: bool = False         # both physical + portal viable (Phase 9)

    # The ranked hypothesis board (Phase 10). Emit the FULL current list every
    # non-terminal turn, ordered rank 1 first = the cheapest discriminating check.
    # Statuses carry across turns, so mark a cause 'rejected' the moment its check
    # comes back against it, and 'supported' when its check confirms it. Never
    # re-list a check the participant has already answered as still 'untested'.
    hypotheses: list[HypothesisEntry] = Field(default_factory=list)

    fault_label: Optional[
        Literal["device_error", "connection_error", "configuration_error"]
    ] = None                     # only when action_type is conclude/escalate


# Quick-reply options the model must never emit: the UI appends its own "Other…"
# chip, and vague non-answers defeat the purpose of showing expected readings.
_NON_OPTION_RE = re.compile(
    r"^(other|others|something else|else|not sure|unsure|i don'?t know|unknown|n/?a)\b[….]*$",
    re.IGNORECASE,
)


def _normalize_reply_options(options: list[str] | None) -> list[str]:
    """Sanitize model-proposed quick-reply options into what the UI renders.

    Keeps only short, distinct, real answers: strips whitespace, drops "Other"-like
    entries (the frontend always appends its own free-text chip), dedupes
    case-insensitively, and caps at 4 so the row never crowds out the input bar.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options or []:
        text = " ".join(str(raw or "").split()).strip(" .!")
        if not text or len(text) > 40 or _NON_OPTION_RE.match(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= 4:
            break
    # A single option is not a choice — it just presses the answer into the
    # participant's hand. Show buttons only when there is a real set to pick from.
    return cleaned if len(cleaned) >= 2 else []


def _merge_hypotheses(
    state: dict[str, Any],
    move_hypotheses: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fold the move's ranked board into persistent state (Phase 10).

    Returns (active_board, rejected_board). The active board is what the model
    emitted this turn, normalized to HypothesisItem shape and re-ranked by list
    order; rejected causes are peeled off into the rejected board (accumulated
    across turns, deduped by cause) so a closed branch is never re-walked. A turn
    that emits no board (e.g. a pure tool loop) leaves the existing one untouched.
    """
    prior_rejected = [
        dict(h) for h in (state.get("rejected_hypotheses", []) or []) if isinstance(h, dict)
    ]
    if not move_hypotheses:
        return list(state.get("hypotheses", []) or []), prior_rejected

    seen_rejected = {str(h.get("description", "")).strip().lower() for h in prior_rejected}
    active: list[dict[str, Any]] = []
    rank = 0
    for entry in move_hypotheses:
        cause = str(getattr(entry, "cause", "") or "").strip()
        if not cause:
            continue
        item = {
            "id": f"H{rank + 1}",
            "description": cause,
            "status": str(getattr(entry, "status", "untested") or "untested"),
            "related_device_id": str(getattr(entry, "device_id", "") or ""),
            "discriminating_check": str(getattr(entry, "discriminating_check", "") or ""),
            "rank": rank + 1,
        }
        if item["status"] == "rejected":
            key = cause.lower()
            if key not in seen_rejected:
                seen_rejected.add(key)
                prior_rejected.append(item)
            continue
        active.append(item)
        rank += 1
    return active, prior_rejected


# DiagnosticMove.action_type -> state.NextActionType. Mostly identity; kept
# explicit so the downstream case-file / timeline logic that keys off
# next_action_type keeps working unchanged.
_MOVE_TO_NEXT_ACTION: dict[str, str] = {
    "ask_clarification": "ask_clarification",
    "physical_check": "physical_check",
    "portal_check": "portal_check",
    "explain": "explain",
    "conclude": "conclude",
    "escalate": "escalate",
}


def _generate_diagnostic_move(
    system_content: str,
    messages: list[Any],
    previous_assistant: str = "",
    fallback_text: str = "",
) -> DiagnosticMove:
    """Force a structured DiagnosticMove from the model (Phase 2, stage 2).

    Falls back to a safe 'explain' move if structured decoding fails, so a
    malformed model response never breaks the turn.
    """
    instruction = (
        f"{system_content}\n\n"
        "You have finished gathering information for this turn. Now emit exactly ONE"
        " DiagnosticMove describing your single next move:\n"
        "- action_type: the one move you are making now.\n"
        "- reply_text: the exact message to send the user (short, warm, one move only).\n"
        "    For a physical_check or portal_check this MUST say WHY, in the message itself,"
        " as one short clause — the user only ever sees reply_text, so a reason that lives"
        " anywhere else does not exist for them. Being asked to do things with no stated"
        " purpose is the single thing participants complain about most.\n"
        "    Give the PURPOSE, never the outcome-mapping:\n"
        '      GOOD: "Try turning the Smart Fan off from its tile and watch the real fan —'
        ' I want to see whether it still takes a command from the dashboard. Did it stop?"\n'
        '      GOOD: "Press the buttons on the fan itself, just to see if the fan itself is'
        ' still alive. What happens?"\n'
        '      BAD (no reason): "Try turning the Smart Fan off from its tile. Did it stop?"\n'
        '      BAD (solves it for them): "Try the tile — if it fails but the buttons work,'
        ' that means it is a connection problem, not the fan."\n'
        "    Say what the check is FOR. Never say what its result would prove.\n"
        "- rationale: the same reason, for the research log. This is NOT shown to the user,"
        " so it never substitutes for putting the purpose in reply_text.\n"
        "- working_hypothesis: your current single most probable CAUSE, as one short falsifiable"
        " phrase ('a rule is not firing', 'the window sensor is not reporting'). Update it every"
        " turn as evidence arrives; your next check should be the one that best tests it. Never"
        " restate the symptom here — a symptom is what happened, a hypothesis is WHY it happened.\n"
        "- hypotheses: the RANKED board of every plausible cause of this symptom. Emit the full"
        " current list every turn (unless you are concluding). Each entry is a cause + the single"
        " cheapest check that would confirm or reject it + a status. ORDER them by cheapest"
        " discriminating check first (rank 1 = the fastest check that settles the most), and make"
        " your reply_text this turn carry out rank 1's check. As answers arrive, set a cause to"
        " 'supported' or 'rejected' and re-rank the rest — never drop a cause silently, and never"
        " re-list an already-answered check as 'untested'. A cause is tested by ITS OWN"
        " discriminating check, never by whether some other rule/device happened to fire.\n"
        "- requested_value: the single value/result the user should report back, if any.\n"
        "- reply_options: ONLY for a physical_check or portal_check whose answer comes from a"
        " small closed set the user just reads off — a state ('Open', 'Closed', 'Unavailable'),"
        " a yes/no observation ('It stopped', 'Still running'), an on/off. Give 2-4 short"
        " options (1-3 words each), mutually exclusive, covering the likely readings. Never"
        " include 'Other', 'Not sure' or similar — the UI adds its own free-text option."
        " Leave EMPTY when the question is open-ended, asks for an explanation, a value you"
        " cannot enumerate (a timestamp, a rule's text), or any answer you cannot predict.\n"
        "- target_device_id: the device this move concerns, if one applies.\n"
        "- operated_device_id: the device you are asking the user to physically handle this"
        " turn — press, flip, unplug, plug in — if any. This is often NOT target_device_id:"
        ' in "unplug the fan from its smart plug, then press the fan\'s own button", the move'
        " concerns the plug (target) but the hands go on the fan (operated). Set it whenever"
        " your reply asks for any hands-on action, and leave it empty otherwise.\n"
        "- fault_label: set ONLY when action_type is conclude or escalate"
        " (device_error / connection_error / configuration_error).\n"
        "Do NOT conclude or escalate without sufficient evidence."
    )
    if previous_assistant:
        instruction += (
            f'\n\nYour previous reply was: "{previous_assistant[:200]}". Do NOT repeat it;'
            " acknowledge the user's latest answer and give the next distinct move."
        )

    structured = init_chat_model(MODEL_NAME, temperature=0).with_structured_output(
        DiagnosticMove
    )
    try:
        move = structured.invoke(
            [{"role": "system", "content": instruction}, *messages]
        )
        if isinstance(move, DiagnosticMove) and (move.reply_text or "").strip():
            return move
    except Exception:
        pass

    return DiagnosticMove(
        action_type="explain",
        reply_text=(fallback_text or "").strip()
        or "Let's double-check what we've confirmed so far — what did the last step show?",
    )


# ── Single-call move (transaction optimization, flag-gated) ────────────────────────────
# A reply turn currently costs TWO big model calls: a tool-decision call whose freeform text
# is discarded, then _generate_diagnostic_move re-sending the whole system prompt to produce
# the structured move. When SMART_HOME_SINGLE_CALL_MOVE is on, DiagnosticMove is bound as a
# tool alongside the info-gathering tools: the model calls an info tool (→ loop) OR the
# DiagnosticMove tool (→ done, structured fields from the args) in ONE call. The downstream
# move-processing is unchanged; only where `move` comes from differs.
#
# Default ON since 2026-07-18: an n=3 A/B on SC-DOOR-WINDOW-OPEN-FAN-OFF held every core metric
# (resolved / root-cause / fault-class all 3/3 in both arms, reached via the correct
# discriminating check) while cutting transactions ~47% (42.0 -> 22.3 calls/run) and reducing
# wandering. Set SMART_HOME_SINGLE_CALL_MOVE=0 to force the old two-call path (the A/B baseline).
_SINGLE_CALL_MOVE = os.getenv("SMART_HOME_SINGLE_CALL_MOVE", "1").lower() in {"1", "true", "yes"}

# langchain names a pydantic tool by its class name.
_SUBMIT_MOVE_NAME = "DiagnosticMove"

# Ported from _generate_diagnostic_move's instruction so the model fills the move as well in
# one call as it did in the dedicated pass. Appended to the system prompt only when the flag
# is on (costs ~a few hundred tokens/call, saves a whole ~12K-token call).
_MOVE_TOOL_GUIDANCE = (
    "\n\nWhen you have finished gathering information and are ready to reply to the user, do"
    " NOT write a plain message — call the DiagnosticMove tool with your single next move."
    " reply_text is the ONLY thing the user sees, so for a physical_check or portal_check it"
    " MUST carry the purpose in one short clause (say what the check is FOR, never what its"
    " result would prove). working_hypothesis is your current single most-probable CAUSE;"
    " emit the full ranked hypotheses board; set fault_label ONLY on conclude/escalate."
    " operated_device_id is the device the user must physically handle this turn, which is"
    ' often NOT target_device_id — in "unplug the fan from its smart plug, then press the'
    ' fan\'s own button" the move concerns the plug but the hands go on the fan. If you'
    " still need to look something up, call an information tool instead — never call an"
    " information tool and DiagnosticMove in the same turn."
)


def _tool_call_name(tc: Any) -> str:
    return str((tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")) or "")


def _tool_call_args(tc: Any) -> dict[str, Any]:
    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
    return args if isinstance(args, dict) else {}


def _move_from_tool_args(args: dict[str, Any], fallback_text: str = "") -> DiagnosticMove:
    """Build a DiagnosticMove from the submit-tool's args; fall back to a safe explain move
    if the args don't validate (keeps a malformed call from breaking the turn)."""
    try:
        move = DiagnosticMove(**(args or {}))
        if (move.reply_text or "").strip():
            return move
    except Exception:
        pass
    return DiagnosticMove(
        action_type="explain",
        reply_text=(fallback_text or "").strip()
        or "Let's double-check what we've confirmed so far — what did the last step show?",
    )


def _rejected_draft_id(state: dict[str, Any], reply_text: str) -> str:
    """Id of the trailing AIMessage carrying `reply_text`, so a rejected draft can be pulled
    back out of the transcript.

    solve_node appends the move as an AIMessage BEFORE the evaluator audits it, so a
    rejected draft is always the newest AI message. Matching on content (not just position)
    keeps this from ever removing an APPROVED earlier reply: if the newest AI message is not
    the draft, nothing is removed at all.
    """
    draft = (reply_text or "").strip()
    if not draft:
        return ""
    for msg in reversed(state.get("messages", []) or []):
        if not isinstance(msg, AIMessage):
            continue
        content = content_to_text(getattr(msg, "content", "")) or ""
        return str(getattr(msg, "id", "") or "") if content.strip() == draft else ""
    return ""


@lru_cache(maxsize=8)
def _get_config_for_scenario(scenario_id: str | None = None) -> dict[str, Any]:
    return build_compiled_config(scenario_id=scenario_id, save_snapshot=False)


def _user_message_count(state: dict[str, Any]) -> int:
    """How many turns the participant has taken, including the one being handled."""
    count = 0
    for msg in state.get("messages", []) or []:
        if isinstance(msg, dict):
            if msg.get("role") == "user":
                count += 1
            continue
        if (getattr(msg, "type", None) or getattr(msg, "role", None)) in ("human", "user"):
            count += 1
    return count


def _latest_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", "")).strip()

        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role in ("human", "user"):
            return str(getattr(msg, "content", "")).strip()

    return ""


def _extract_tool_messages(state: dict[str, Any]) -> list[Any]:
    result = []

    for msg in state.get("messages", []):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            result.append(msg)
            continue

        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role == "tool":
            result.append(msg)

    return result


def _tool_message_name(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("name", "unknown_tool"))

    return str(getattr(msg, "name", "unknown_tool"))


def _tool_message_content(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("content", ""))

    return str(getattr(msg, "content", ""))


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _classify_user_move(user_text: str) -> str:
    """Classify one user turn into a strategy signal for running tallies."""
    text = user_text.lower()

    connection_words = [
        "offline", "wifi", "wi-fi", "network", "router", "hub",
        "bridge", "zigbee", "signal", "connectivity",
        "disconnected", "not connected", "paired", "unpaired",
    ]
    logic_words = [
        "when", "after", "if ", "then", "automation", "routine",
        "trigger", "scene", "schedule", "rule", "condition",
        "supposed to", "should turn",
    ]
    device_words = [
        "lamp", "light", "bulb", "thermostat", "sensor", "blind",
        "curtain", "tablet", "plug", "switch",
        "power", "button", "remote",
    ]

    has_connection = any(w in text for w in connection_words)
    has_logic = any(w in text for w in logic_words)
    has_device = any(w in text for w in device_words)

    if has_logic and (has_device or has_connection):
        return "thread_step"
    if has_logic:
        return "thread_step"
    if has_connection and has_device:
        return "thread_step"
    if has_connection:
        return "connection_focus"
    if has_device:
        return "device_focus"
    return "neutral"


_STRATEGY_TO_MINDMAP: dict[str, str] = {
    "devices_first": "device_level_diagnosis",
    "connections_first": "connection_level_diagnosis",
    "follow_the_thread": "dependency_chain_diagnosis",
    "unknown": "unknown",
}


def _update_strategy_observation(state: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Update running strategy signals from one user turn; derive the dominant label."""
    signals: dict[str, int] = dict(state.get("strategy_signals") or {})
    for k in ("devices_first", "connections_first", "follow_the_thread"):
        signals.setdefault(k, 0)

    move = _classify_user_move(user_text)
    if move == "device_focus":
        signals["devices_first"] += 1
    elif move == "connection_focus":
        signals["connections_first"] += 1
    elif move == "thread_step":
        signals["follow_the_thread"] += 1

    if max(signals.values()) == 0:
        observed: str = "unknown"
    else:
        observed = max(signals, key=lambda k: signals[k])

    history: list[dict] = list(state.get("strategy_history") or [])
    history.append({
        "turn": len(history) + 1,
        "user_text": user_text[:240],
        "move": move,
        "signals": dict(signals),
        "running_label": observed,
    })

    return {
        "strategy_signals": signals,
        "observed_strategy": observed,
        "suspected_strategy": observed,
        "selected_mindmap": _STRATEGY_TO_MINDMAP.get(observed, "unknown"),
        "strategy_history": history,
    }


_NO_SUSPECT_PHRASES = (
    "not sure", "no idea", "don't know", "do not know", "dunno", "no clue",
    "you tell me", "help me", "i don't know", "idk", "not really", "no suspect",
    "nothing", "none", "no guess",
)
_NO_SUSPECT_EXACT = {"no", "nope", "nah", "not really", "no.", "n/a", "na"}


def _classify_primary_suspect(user_text: str) -> tuple[str, str]:
    """Parse the user's stated primary suspect into (type, label).

    Returns ("unknown", "") when the participant declines to name a suspect
    (e.g. "no", "not sure") so a bare "no" is never stored as if it were a
    culprit.
    """
    t = user_text.lower().strip()

    # No suspect offered — an outright "no" or an "I don't know" style answer.
    if t.strip(" .!?") in _NO_SUSPECT_EXACT or any(p in t for p in _NO_SUSPECT_PHRASES):
        return "unknown", ""

    if any(w in t for w in ["wifi", "wi-fi", "network", "hub", "router",
                            "connection", "offline", "zigbee"]):
        return "connection", user_text.strip()
    if any(w in t for w in ["rule", "automation", "routine", "schedule",
                            "trigger", "condition", "scene"]):
        return "logic", user_text.strip()
    if any(w in t for w in ["lamp", "light", "device", "sensor", "thermostat",
                            "blind", "curtain", "plug", "tablet"]):
        return "device", user_text.strip()
    return "unknown", user_text.strip()


# Filler words stripped before device-name matching, so verbose phrasing like
# "the door light, like I already said" reduces to its meaningful tokens.
_RESOLVE_STOPWORDS = {
    "the", "a", "an", "my", "is", "it", "that", "this", "like", "i", "already",
    "said", "as", "just", "please", "you", "your", "know", "im", "was", "to",
    "on", "of", "in", "and", "then", "so", "actually", "again", "check", "can",
    "could", "would", "one", "about", "still",
}


def _despace(text: str) -> str:
    """Alphanumeric-only, lowercased key so 'Bedlight R' and 'bed light r' align."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _meaningful_key(text: str) -> str:
    """Despaced key of a user utterance with filler words removed."""
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", str(text).lower())
        if t not in _RESOLVE_STOPWORDS
    ]
    return "".join(tokens)


def _device_match_keys(device: dict[str, Any]) -> list[str]:
    strings = [device.get("id", ""), device.get("name", "")]
    strings += list(device.get("aliases", []) or [])
    return [key for key in (_despace(s) for s in strings) if key]


def _fuzzy_resolve_device(
    config: dict[str, Any],
    text: str,
) -> tuple[str, str, str, list[str]]:
    """Resolve a device from messy free text (Phase 2b).

    Fixes the pilot failures where extra words ("the door light, like I already
    said") or a partial hint ("bed light R") defeated exact/substring matching.
    A device matches "strongly" when the user typed at least a full alias of it
    (exact, or the alias appears inside the utterance once spacing is ignored);
    a unique strong match resolves, several become disambiguation candidates.
    A conservative typo pass (high-threshold, single winner) catches misspellings.
    Returns ("","","",[]) to defer to the caller's keyword map when unsure.
    """
    devices = config["environment"].get("devices", [])
    user_key = _meaningful_key(text)
    if len(user_key) < 2:
        return "", "", "", []

    strong: list[dict[str, Any]] = []
    for device in devices:
        for key in _device_match_keys(device):
            # exact, or the full alias sits inside the utterance (min length 5
            # guards short tokens like "tv"/"fan"/"hub" from matching mid-word).
            if key == user_key or (len(key) >= 5 and key in user_key):
                strong.append(device)
                break

    if len(strong) == 1:
        d = strong[0]
        return str(d.get("id", "")), str(d.get("name", "")), str(d.get("type", "")), []
    if len(strong) > 1:
        return "", "", "", [str(d.get("id", "")) for d in strong]

    # Typo tolerance: only when a single device is clearly closest and well above
    # both the threshold and the runner-up, so misspellings resolve but ambiguous
    # input still defers to the keyword map / disambiguation prompt.
    scored = sorted(
        (
            (max(SequenceMatcher(None, user_key, key).ratio() for key in keys), device)
            for device in devices
            if (keys := _device_match_keys(device))
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.86 and (
        len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
    ):
        d = scored[0][1]
        return str(d.get("id", "")), str(d.get("name", "")), str(d.get("type", "")), []

    return "", "", "", []


def _resolve_device_from_text(
    config: dict[str, Any],
    text: str,
) -> tuple[str, str, str, list[str]]:
    environment = config["environment"]

    direct = find_device(environment, text)
    if direct:
        return (
            str(direct.get("id", "")),
            str(direct.get("name", "")),
            str(direct.get("type", "")),
            [],
        )

    # Phase 2b: fuzzy/despaced pass before the coarse keyword map, so verbose or
    # partial phrasing resolves instead of falling through to a generic list.
    fuzzy_id, fuzzy_name, fuzzy_type, fuzzy_candidates = _fuzzy_resolve_device(config, text)
    if fuzzy_id:
        return fuzzy_id, fuzzy_name, fuzzy_type, []
    if fuzzy_candidates:
        return "", "", "", fuzzy_candidates

    lowered = text.lower()

    keyword_map = {
        "wall light": ["doorlight", "windowlight"],
        "wall-mounted light": ["doorlight", "windowlight"],
        "door light": ["doorlight"],
        "window light": ["windowlight"],
        "light": [
            "doorlight",
            "windowlight",
            "floorlamp",
            "bedlight_l",
            "bedlight_r",
        ],
        "floor lamp": ["floorlamp"],
        "standing lamp": ["floorlamp"],
        "bedside lamp": ["bedlight_l", "bedlight_r"],
        "left bedside": ["bedlight_l"],
        "right bedside": ["bedlight_r"],
        "thermostat": ["heater"],
        "heater": ["heater"],
        "heating": ["heater"],
        "temperature": ["temperaturesensor", "heater"],
        "humidity": ["temperaturesensor"],
        "presence": ["presencesensor"],
        "motion": ["presencesensor"],
        "door sensor": ["doorsensor"],
        "window sensor": ["windowsensor"],
        "blind": ["rollo"],
        "blinds": ["rollo"],
        "rollo": ["rollo"],
        "roller shutter": ["rollo"],
        "curtain": ["curtain"],
        "curtains": ["curtain"],
        "hub": ["smart_hub"],
        # The IR blaster and the TV smart plug were dropped from this study, so a
        # mention of either resolves to the TV itself — the device the participant
        # can actually see and operate.
        "tv plug": ["tv"],
        "tv socket": ["tv"],
        "tv": ["tv"],
        "fan plug": ["socket_fan"],
        "fan socket": ["socket_fan"],
        "fan": ["smartfan"],
    }

    candidate_ids: list[str] = []

    for keyword, ids in keyword_map.items():
        if keyword in lowered:
            candidate_ids.extend(ids)

    candidate_ids = list(dict.fromkeys(candidate_ids))

    if len(candidate_ids) == 1:
        device = find_device(environment, candidate_ids[0])
        if device:
            return (
                str(device.get("id", "")),
                str(device.get("name", "")),
                str(device.get("type", "")),
                [],
            )

    if len(candidate_ids) > 1:
        return "", "", "", candidate_ids

    candidates = find_device_candidates(environment, text)
    if len(candidates) == 1:
        device = candidates[0]
        return (
            str(device.get("id", "")),
            str(device.get("name", "")),
            str(device.get("type", "")),
            [],
        )

    if len(candidates) > 1:
        return "", "", "", [str(d.get("id", "")) for d in candidates]

    return "", "", "", []


def _candidate_labels(config: dict[str, Any], candidate_ids: list[str]) -> list[str]:
    labels = []

    for candidate_id in candidate_ids:
        device = find_device(config["environment"], candidate_id)
        if device:
            labels.append(f"{device.get('name')} ({device.get('location_hint')})")

    return labels


def _pick_best_candidate(
    config: dict[str, Any], candidate_ids: list[str], text: str
) -> str:
    """The candidate whose name/aliases best match `text` — used to COMMIT to a device when a
    disambiguation was already asked once and the participant did not pick.

    Re-asking the same "which one do you mean?" verbatim is what abandoned a pilot session
    (4bc1a5cc: "I already described it to you", agent repeated the identical question, session
    died). Better to commit to the most likely referent than to loop. Scored by token overlap
    first (so "door lamp" → Door Light on the shared "door"), fuzzy ratio as tie-break."""
    words = set(re.findall(r"\w+", (text or "").lower()))
    best_id, best_score = "", -1.0
    for cid in candidate_ids:
        device = find_device(config["environment"], cid)
        if not device:
            continue
        terms = [str(device.get("name", "")), *[str(a) for a in device.get("aliases", [])]]
        # Count total OCCURRENCES of the participant's words across all of this device's
        # names/aliases, not just how many distinct words match. This is what separates a real
        # tie from an apparent one: "door lamp" hits "door" once for Floor Lamp (never) but many
        # times for Door Light, whose aliases repeat "door" — so the distinctive word the
        # participant actually chose dominates the generic "lamp" both devices share.
        occurrences = sum(
            1
            for t in terms
            for tok in re.findall(r"\w+", t.lower())
            if tok in words
        )
        fuzzy = max(
            (SequenceMatcher(None, (text or "").lower(), t.lower()).ratio() for t in terms),
            default=0.0,
        )
        score = occurrences + fuzzy
        if score > best_score:
            best_id, best_score = cid, score
    return best_id or (candidate_ids[0] if candidate_ids else "")


def _deduplicate_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []

    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def _summarize_tool_result(parsed: dict[str, Any]) -> str:
    tool_name = parsed.get("tool", "tool")

    if tool_name == "get_device_info":
        device = parsed.get("device", {})
        return (
            f"{device.get('name')} is a {device.get('type')} "
            f"using {device.get('protocol')}."
        )

    if tool_name == "get_device_knowledge":
        device = parsed.get("device", {})
        return f"Knowledge retrieved for {device.get('name')}."

    if tool_name == "check_hub_status":
        hub = parsed.get("hub", {})
        return f"{hub.get('name')} status is {hub.get('status')}."

    if tool_name == "check_device_connectivity":
        device = parsed.get("device", {})
        return f"Connectivity dependencies checked for {device.get('name')}."

    if tool_name in {"show_device_location", "show_hub_location"}:
        return parsed.get("user_instruction", "Visual guidance retrieved.")

    if tool_name == "get_portal_entity_info":
        device = parsed.get("device", {})
        entity = parsed.get("portal_entity", {})
        return (
            f"Dashboard entity retrieved for {device.get('name')}: "
            f"{entity.get('entity_id', '')}."
        )

    if tool_name == "get_device_portal_check_guide":
        device = parsed.get("device", {})
        return f"Dashboard check guide retrieved for {device.get('name')}."

    if tool_name == "get_portal_task_guide":
        return f"Dashboard task guide retrieved: {parsed.get('task_id', '')}."

    if tool_name == "get_portal_overview":
        return "Dashboard overview retrieved."

    if tool_name == "list_devices":
        return f"Listed {parsed.get('count', 0)} devices."

    if tool_name == "list_portal_tasks":
        return f"Listed {parsed.get('count', 0)} dashboard tasks."

    return json.dumps(parsed, ensure_ascii=False)


def _extract_tool_state_updates(state: dict[str, Any]) -> dict[str, Any]:
    existing_tool_records = state.get("checked_tools", []) or []
    existing_keys = {
        (record.get("tool_name", ""), str(record.get("tool_result", "")))
        for record in existing_tool_records
        if isinstance(record, dict)
    }

    evidence = list(state.get("evidence", []) or [])
    confirmed_facts = list(state.get("confirmed_facts", []) or [])
    visual_outputs = list(state.get("visual_outputs", []) or [])
    checked_tools = list(existing_tool_records)
    checked_devices = list(state.get("checked_devices", []) or [])
    portal_context = dict(state.get("portal_context", {}) or {})

    for msg in _extract_tool_messages(state):
        tool_name = _tool_message_name(msg)
        content = _tool_message_content(msg)
        key = (tool_name, content)

        if key in existing_keys:
            continue

        parsed = _try_parse_json(content)

        checked_tools.append(
            {
                "tool_name": tool_name,
                "tool_args": {},
                "tool_result": content,
                "success": bool(parsed is None or parsed.get("found", True)),
            }
        )

        if not parsed:
            evidence.append(
                {
                    "source": "tool",
                    "content": f"{tool_name}: {content}",
                    "status": "reported",
                    "related_tool": tool_name,
                }
            )
            continue

        device = parsed.get("device") or {}
        device_id = device.get("id", "")

        if device_id and device_id not in checked_devices:
            checked_devices.append(device_id)

        if parsed.get("found") is True:
            item = {
                "source": "tool",
                "content": _summarize_tool_result(parsed),
                "status": "verified",
                "related_device_id": device_id,
                "related_tool": tool_name,
            }
            evidence.append(item)
            confirmed_facts.append(item)

        if parsed.get("found") is False:
            evidence.append(
                {
                    "source": "tool",
                    "content": parsed.get(
                        "message",
                        f"{tool_name} did not find a result.",
                    ),
                    "status": "unknown",
                    "related_tool": tool_name,
                }
            )

        visual_output = parsed.get("visual_output")
        if isinstance(visual_output, dict):
            visual_outputs.append(
                {
                    "type": visual_output.get("type", "floorplan_position_image"),
                    "path": visual_output.get("path", ""),
                    "configured_path": visual_output.get("configured_path", ""),
                    "absolute_path": visual_output.get("absolute_path", ""),
                    "exists": bool(visual_output.get("exists", False)),
                    "data_url": visual_output.get("data_url"),
                    "related_device_id": visual_output.get("related_device_id") or device_id,
                    "caption": visual_output.get("caption")
                    or parsed.get("user_instruction", ""),
                }
            )

        portal_instruction = parsed.get("portal_instruction")
        if isinstance(portal_instruction, dict):
            portal_context.update(
                {
                    "portal_available": True,
                    "portal_mode": "static_guide",
                    "last_portal_instruction": json.dumps(
                        portal_instruction,
                        ensure_ascii=False,
                    ),
                    "relevant_screen": portal_instruction.get("recommended_tab", ""),
                    "requested_values": portal_instruction.get("user_should_report", []),
                    "related_device_id": device_id,
                }
            )

    return {
        "evidence": _deduplicate_dicts(evidence),
        "confirmed_facts": _deduplicate_dicts(confirmed_facts),
        "checked_tools": checked_tools,
        "checked_devices": list(dict.fromkeys(checked_devices)),
        "visual_outputs": _deduplicate_dicts(visual_outputs),
        "portal_context": portal_context,
    }


_GREETING_REPLY = "Hello! Is there something in your smart home that's not working as expected?"

# Fallbacks if the suspect_elicitation_policy text is missing from the config.
_DEFAULT_SUSPECT_QUESTION = (
    "Okay, something's clearly off. What's your gut feeling about what's causing it"
    " - and have you tried anything yourself yet?"
)

# First-person actions in a symptom report: "I closed the window", "I turned on the TV and",
# "after opening the door", "I've already tried...". If the participant's FIRST message
# contains these, they have already told us what they did - so the canned opener must not end
# with "have you tried anything yourself yet?". Deterministic on purpose: the opener itself is
# deterministic (emitted before any model runs), so its fix has to live at the same level.
_PRIOR_ACTIONS_RE = re.compile(
    r"\b(i|we)('ve|'d)?(\s+\w+){0,2}\s+"
    r"(closed?|open(ed)?|turn(ed)?|switch(ed)?|press(ed)?|toggl(ed)?|tri(ed)?|"
    r"set|moved?|check(ed)?|restart(ed)?|enter(ed)?)\b"
    r"|\bafter\s+(closing|opening|turning|switching|pressing|entering)\b",
    re.IGNORECASE,
)
_DEFAULT_PRIOR_ACTIONS_QUESTION = (
    "Got it. Have you already tried anything yourself so far?"
)


# Clarify must ASK, never INSTRUCT — but the model occasionally disobeys and issues a
# real check (session 3535204e: first reply was "try turning the Smart Fan off from its
# tile and tell me if the real fan stops"). Because clarify ends at END, that check
# bypassed the evaluator and the timeline AND left diagnosis_started false — so the
# canned "what's your gut feeling?" opener fired a turn late, AFTER the participant had
# already run a check. The prompt now forbids instructing; this deterministic backstop
# makes a leaked check at least COUNT as one (last_check_requested), so the elicitation
# guard and the answered-check capture see reality even when the model disobeys.
_CHECK_LIKE_RE = re.compile(
    r"\b(try (turning|pressing|switching|toggling)"
    r"|turn (it|the) \w+ (on|off)"
    r"|press (the|its|on)"
    r"|toggle"
    r"|tap (the|on|it)"
    r"|dashboard\s*>"
    r"|(on|in|go to) the dashboard"
    r"|all (devices|rules)"
    r"|read me"
    r"|tell me (its|the) (exact )?(state|value|status))\b",
    re.IGNORECASE,
)


def clarify_node(state: dict[str, Any]) -> dict[str, Any]:
    # Pure greeting — no LLM call needed, just say hi back
    if state.get("is_greeting"):
        return {
            "messages": [{"role": "assistant", "content": _GREETING_REPLY}],
            "pending_question": _GREETING_REPLY,
            "diagnosis_phase": "symptom_capture",
            "next_action_type": "ask_clarification",
            "last_agent_reply": _GREETING_REPLY,
            "is_greeting": False,
            "reply_options": [],
        }

    scenario_id = state.get("active_scenario_id")
    config = _get_config_for_scenario(scenario_id)

    user_text = _latest_user_text(state)
    reported_symptom = state.get("reported_symptom") or user_text

    model = init_chat_model(MODEL_NAME, temperature=0)
    system_prompt = build_runtime_system_prompt(config, state)

    response = model.invoke(
        [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "Clarification mode:\n"
                    "- Ask exactly ONE short clarifying question.\n"
                    "- Do not give advice yet.\n"
                    "- Do not list possible causes.\n"
                    "- NEVER instruct the user to operate, press, toggle, open, or read"
                    " anything from a device or the dashboard. Sending them to check"
                    " something is a diagnostic move, and it is not yours to make in"
                    " clarification mode — ask what they OBSERVED instead.\n"
                    "- The question should identify the affected device, symptom, or user goal.\n"
                    "- There is exactly one of each device in this room. Do not ask the user to pick between instances that do not exist (e.g. there is only one roller shutter). If the symptom already points to a single device, ask about what they observed instead of which one."
                ),
            },
            *state.get("messages", []),
        ]
    )

    content = content_to_text(getattr(response, "content", "")).strip()

    updates: dict[str, Any] = {
        # Store a clean text message, not the raw Responses-API block list.
        "messages": [AIMessage(content=content)],
        "reported_symptom": reported_symptom,
        "pending_question": content,
        "diagnosis_phase": "symptom_capture",
        "next_action_type": "ask_clarification",
        "last_agent_reply": content,
        "reply_options": [],
    }
    # Backstop (see _CHECK_LIKE_RE above): a clarification that actually instructs a
    # check must be tracked as one, or downstream guards think nothing happened yet.
    if _CHECK_LIKE_RE.search(content):
        updates["last_check_requested"] = content
    return updates


def locate_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic location/photo path.

    For "where is X / show me X on the map" turns: resolve the device and return
    its floorplan image directly, with no LLM call and no suspect elicitation.
    If the device is ambiguous or unresolved, ask one short clarifying question.
    """
    scenario_id = state.get("active_scenario_id")
    config = _get_config_for_scenario(scenario_id)

    user_text = _latest_user_text(state)
    resolved_id, resolved_name, resolved_type, candidates = _resolve_device_from_text(
        config,
        user_text,
    )

    if not resolved_id:
        if candidates:
            labels = _candidate_labels(config, candidates)
            question = "Which one do you mean: " + " or ".join(labels) + "?"
        else:
            question = "Which device would you like me to point out on the floorplan?"

        return {
            "messages": [{"role": "assistant", "content": question}],
            "diagnosis_phase": "entity_resolution",
            "pending_question": question,
            "next_action_type": "ask_clarification",
            "ambiguous_device_candidates": candidates,
            "last_agent_reply": question,
            # "Which one do you mean?" has a genuinely closed answer set — the
            # candidate device names themselves — so offer them as buttons.
            "reply_options": _normalize_reply_options(labels) if candidates else [],
        }

    parsed = _try_parse_json(show_device_location.invoke({"device_name_or_id": resolved_id})) or {}
    instruction = parsed.get(
        "user_instruction",
        f"I marked {resolved_name} on the floorplan below.",
    )

    visual_outputs = list(state.get("visual_outputs", []) or [])
    visual = parsed.get("visual_output")
    if isinstance(visual, dict):
        visual_outputs.append(
            {
                "type": visual.get("type", "floorplan_position_image"),
                "path": visual.get("path", ""),
                "configured_path": visual.get("configured_path", ""),
                "absolute_path": visual.get("absolute_path", ""),
                "exists": bool(visual.get("exists", False)),
                "data_url": visual.get("data_url"),
                "related_device_id": visual.get("related_device_id") or resolved_id,
                "caption": visual.get("caption") or instruction,
            }
        )
        visual_outputs = _deduplicate_dicts(visual_outputs)

    return {
        "messages": [{"role": "assistant", "content": instruction}],
        "target_device": resolved_name,
        "target_device_id": resolved_id,
        "target_device_type": resolved_type,
        "ambiguous_device_candidates": [],
        "diagnosis_phase": "visual_guidance",
        "next_action_type": "visual_guidance",
        "visual_outputs": visual_outputs,
        "last_agent_reply": instruction,
        "reply_options": [],
    }


def _previous_assistant_text(messages: list[Any]) -> str:
    """Text of the most recent assistant message already in the history.

    In solve_node this is the agent's previous turn (the new response has not
    been appended yet), so it's what a fresh reply must not simply repeat.
    """
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "type", None) or getattr(msg, "role", None)
            content = getattr(msg, "content", "")

        if role in ("ai", "assistant"):
            return str(content or "").strip()

    return ""


# The agent opens most replies by echoing the participant's last result ("Yep, got it —
# unavailable.", "Good — the window sensor looks right."). That preamble is about the
# PREVIOUS step, so classifying the whole reply lets the last result contaminate the label of
# the next action: "Yep, got it — unavailable. Please press the buttons on the Smart Fan"
# came out as "Connectivity" purely because the word "unavailable" was echoed, when it is
# plainly a hardware check. Strip the acknowledgement before classifying.
_ACK_PREAMBLE = re.compile(
    r"^\s*(?:ok(?:ay)?|yep|yes|right|good|nice|great|perfect|thanks|thank you|got it|"
    r"fair point|understood|noted|alright)\b[^.!?]*[.!?—-]\s*",
    re.IGNORECASE,
)


def _action_clause(reply: str) -> str:
    """The part of a reply that is the ACTION, with the acknowledgement stripped."""
    text = (reply or "").strip()
    # Peel at most two leading acknowledgements ("Okay — got it. Now, ...").
    for _ in range(2):
        stripped = _ACK_PREAMBLE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text or (reply or "")


# The screens a check can send a participant to, longest first so "Your Room > Devices" is
# matched before the bare "Your Room".
_PORTAL_SCREENS = [
    "Your Room > Connections",
    "Your Room > Devices",
    "Your Room > Rules",
    "All Devices",
    "All Rules",
    "Your Room",
]


def _screen_from_instruction(instruction: str) -> str:
    """The portal screen an agent instruction sent the participant to.

    Used to remember where they are, so the next turn does not move them somewhere else for
    a value that is already in front of them.
    """
    text = (instruction or "").lower()
    for screen in _PORTAL_SCREENS:
        if screen.lower() in text:
            return screen
    return ""


def _device_in_action(action_text: str, environment: dict[str, Any] | None) -> str:
    """The device this ACTION is about, which is not always the device under suspicion.

    A session about the Smart Fan can still contain a step that reads the Window Sensor's
    state. Labelling that row "Smart Fan state" tells the participant the wrong story about
    what they just did. Prefer the device the action actually names; fall back to the
    device under investigation.
    """
    if not environment:
        return ""

    text = (action_text or "").lower()
    best = ""
    for device in environment.get("devices", []) or []:
        names = [str(device.get("name", ""))] + [str(a) for a in device.get("aliases", []) or []]
        for name in names:
            n = name.strip().lower()
            # Long names first: "Fan Smart Plug" must win over "Fan".
            if len(n) >= 4 and n in text and len(n) > len(best):
                best = name if name == device.get("name") else str(device.get("name", ""))
    return best


def _hypothesis_label(
    action_text: str,
    target_device: str,
    suspect_label: str,
    suspect_type: str,
    environment: dict[str, Any] | None = None,
) -> str:
    """Deterministic label for the hypothesis a given action is testing.

    This is the Case File rail the participant reads, so a wrong label is not cosmetic — it
    is the study telling them a false story about their own reasoning.

    Two bugs fixed here, both visible in a real session:

    1. It classified the whole reply, INCLUDING the agent's echo of the last result, so a
       "press the buttons on the fan" check was filed under Connectivity. Now classified on
       the action clause only (see _action_clause).

    2. It fell back to `suspect_label`, which is captured as RAW PARTICIPANT TEXT. A plain
       "read me the State" action matched no bucket, so three consecutive rows in the rail
       were titled "the device is unavailable." — a user utterance, used as a hypothesis
       name. The fallback is now a real category, and a free-text suspect is only used when
       it actually looks like a label rather than a sentence.
    """
    clause = _action_clause(action_text)
    t = clause.lower()
    # The device this step is actually about — not necessarily the one under suspicion.
    device = _device_in_action(clause, environment) or (target_device or "").strip()

    if any(w in t for w in ["rule", "automation", "routine", "trigger",
                            "condition", "scene", "schedule"]):
        return "Automation / rule"
    # Physical/manual checks BEFORE connectivity: an instruction to press the device's own
    # buttons is a hardware check even if the sentence also mentions the word "unavailable".
    if any(w in t for w in ["physical", "switch", "button", "on the device itself",
                            "on the device", "press", "by hand", "manually"]):
        return f"{device} hardware" if device else "Device hardware"
    if any(w in t for w in ["unavailable", "offline", "reach", "reaching",
                            "connectivity", "connection", "wi-fi", "wifi",
                            "network", "hub", "zigbee"]):
        return "Connectivity"
    if any(w in t for w in ["toggle", "turn it on", "turn on", "turn it off", "turn off",
                            "operate", "move the", "room view", "responds", "respond"]):
        return f"{device} (device)" if device else "Device"
    # The single most common action the agent issues — and previously the one with no bucket
    # at all, which is how raw user text ended up as a row title.
    if any(w in t for w in ["state", "last changed", "read me", "value", "shows",
                            "all devices"]):
        return f"{device} state" if device else "Device state"

    # Only use a participant-named suspect if it reads like a LABEL, not a sentence. A
    # captured reply such as "the device is unavailable." is a result, not a hypothesis.
    label = (suspect_label or "").strip().rstrip(".")
    if label and len(label.split()) <= 4 and suspect_type not in ("", "unknown"):
        return label

    return device or "Device"


# Ways a reply can already be carrying its purpose. If none of these appear, the message is
# a bare order, and the participant is being told to go and do something with no idea why.
_PURPOSE_MARKERS = (
    "to see", "to check", "to find out", "to confirm", "to rule out", "to tell",
    "so i ", "so we ", "so that", "because", "just to", "i want to", "i'd like to",
    "let's find out", "want to know", "makes sure", "make sure", "to know",
)


def _ensure_purpose(reply: str, rationale: str, action_type: str) -> str:
    """Guarantee a check tells the participant WHY.

    The model now writes the purpose into reply_text, but a check that arrives without one is
    the single thing participants push back on ("I wish I knew your intention to ask this"),
    so do not leave it to chance. If the reply is a bare imperative and we have a rationale,
    fold it in — before the closing question, where it reads as a reason rather than an
    afterthought.
    """
    text = (reply or "").strip()
    reason = " ".join((rationale or "").split()).strip()

    if action_type not in {"physical_check", "portal_check"}:
        return text
    if not text or not reason:
        return text
    if any(m in text.lower() for m in _PURPOSE_MARKERS):
        return text

    reason = reason[0].lower() + reason[1:] if reason else reason
    reason = reason.rstrip(".")

    # Keep the trailing question last: "<action> — <why>. <question>?"
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1 and sentences[-1].rstrip().endswith("?"):
        body = " ".join(sentences[:-1]).rstrip()
        question = sentences[-1].strip()
        body = body.rstrip(".")
        return f"{body} — {reason}. {question}"

    return f"{text.rstrip('.')} — {reason}."


def _clean_action_text(text: str) -> str:
    """Trim an agent action reply to a compact one-line timeline entry."""
    collapsed = " ".join(str(text).split())
    return collapsed[:240]


def _update_diagnostic_checklist(
    state: dict[str, Any],
    reported_symptom: str,
    fault_label: str = "",
) -> list[dict[str, Any]]:
    """Advance the fixed diagnostic checklist from current state (Phase 3).

    Conservative: only items unambiguous from state are auto-answered — portal
    condition, first report, and (once the agent concludes) the fault label. The
    remaining items are advanced by Phase 2's structured DiagnosticMove as that
    matures. Never overwrites an item already marked answered.
    """
    checklist = [dict(item) for item in (state.get("diagnostic_checklist", []) or [])]
    if not checklist:
        return checklist

    def _answer(item_id: str, answer: str) -> None:
        if not answer:
            return
        for item in checklist:
            if item.get("id") == item_id and item.get("status") != "answered":
                item["status"] = "answered"
                item["answer"] = answer
                return

    _answer("portal_condition", state.get("presentation_mode", ""))
    _answer("first_report", reported_symptom)
    _answer("fault_label", fault_label)
    return checklist


def solve_node(state: dict[str, Any]) -> dict[str, Any]:
    scenario_id = state.get("active_scenario_id")
    config = _get_config_for_scenario(scenario_id)

    messages = state.get("messages", [])
    user_text = _latest_user_text(state)
    reported_symptom = state.get("reported_symptom") or user_text

    # Silently observe strategy on every turn — never ask the user about it
    obs_updates = _update_strategy_observation(state, user_text)

    tool_updates = _extract_tool_state_updates(state)

    # --- Capture the user's spoken answer to the previous check -------------
    # The deterministic tool-ingestion above only reads *tool* messages, so a
    # value the user reports in plain text (e.g. "there are four rules") is
    # otherwise never recorded. Without this the graph can't tell an answered
    # check from an unanswered one, and a temperature=0 solve may re-issue the
    # identical request. Skip suspect / prior-action turns — those are consumed
    # by their own handlers below, not treated as dashboard observations.
    outstanding_check = (
        (state.get("last_check_requested") or "").strip()
        or (state.get("pending_question") or "").strip()
    )
    answered_previous_check = (
        bool(outstanding_check)
        and bool(user_text)
        and not state.get("awaiting_suspect_answer", False)
        and not state.get("awaiting_prior_actions", False)
    )
    if answered_previous_check:
        portal_ctx = dict(tool_updates.get("portal_context", {}) or {})
        portal_ctx["last_portal_observation"] = user_text
        # Where the participant is standing RIGHT NOW: the screen the last check sent them
        # to. Nothing recorded this, so build_portal_shortest_path_block — which exists
        # precisely to stop the agent bouncing people between tabs for values that are
        # already on their screen — could never fire. That is how a participant ended up
        # being sent to All Devices to re-read a State they had just read on the floor map.
        screen = _screen_from_instruction(outstanding_check)
        if screen:
            portal_ctx["relevant_screen"] = screen
        tool_updates["portal_context"] = portal_ctx

        evidence_list = list(tool_updates.get("evidence", []) or [])
        evidence_list.append(
            {
                "source": "user",
                # Keep the instruction near-whole: the evaluator's duplicate check compares
                # a new move against THIS embedded text, and a 120-char cut used to chop off
                # the requested value ("...tell me if the real f") so re-asks slipped through.
                "content": (
                    f"User reported (re: {outstanding_check[:300]}): {user_text}"
                ),
                "status": "reported",
            }
        )
        tool_updates["evidence"] = _deduplicate_dicts(evidence_list)

    # --- Deduction timeline: close the open step with the reported result ---
    # A step is opened when the agent issues an action (below); it stays open
    # until the participant answers. Only an answer to an actual action check
    # (last_check_requested) closes it — not an answer to a suspect/clarify Q.
    deduction_timeline: list[dict[str, Any]] = [
        dict(step) for step in (state.get("deduction_timeline", []) or [])
    ]
    answered_action = bool((state.get("last_check_requested") or "").strip()) and (
        answered_previous_check
    )
    if answered_action and deduction_timeline and not deduction_timeline[-1].get("result"):
        deduction_timeline[-1]["result"] = user_text

    # --- Primary suspect / participant mind-map capture --------------------
    # Two ways the primary suspect can be set:
    #   1. "volunteered" — the participant named a likely culprit in their
    #      symptom description (works even when the policy below is disabled).
    #   2. "elicited"    — the participant answered the agent's suspect question
    #      (only when suspect_elicitation_policy.enabled is true).
    # The source is recorded so the study can tell a spontaneous mind-map from
    # one the agent had to draw out.
    elicitation_policy = config["behavior"].get("suspect_elicitation_policy", {}) or {}
    elicitation_enabled = bool(elicitation_policy.get("enabled", False))
    ask_only_if_none = bool(
        elicitation_policy.get("ask_only_if_no_suspect_volunteered", True)
    )

    primary_suspect_label: str = state.get("primary_suspect_label", "") or ""
    primary_suspect_type: str = state.get("primary_suspect_type", "unknown") or "unknown"
    suspect_source: str = state.get("suspect_source", "unknown") or "unknown"
    suspect_elicitation_done = bool(state.get("suspect_elicitation_done", False))
    awaiting_suspect_answer = bool(state.get("awaiting_suspect_answer", False))

    prior_actions = state.get("prior_actions", "") or ""
    prior_actions_asked = bool(state.get("prior_actions_asked", False))
    awaiting_prior_actions = bool(state.get("awaiting_prior_actions", False))

    if awaiting_suspect_answer:
        # The participant is answering the agent's suspect question right now.
        # Capture their answer verbatim as their stated suspect (their mind map),
        # even if the keyword classifier is too coarse to type it precisely.
        ps_type, ps_label = _classify_primary_suspect(user_text)
        if ps_type == "unknown":
            # No CAUSE named — they restated the symptom, or said they don't know. That is a
            # legitimate answer and it is NOT a suspect: shoving their whole message into
            # primary_suspect_label put a verbatim echo where the case file expects a
            # hypothesis. Record that they offered none; the agent's own working_hypothesis
            # (updated on every DiagnosticMove) carries the current best cause instead.
            suspect_source = "none"
        else:
            primary_suspect_label = ps_label or user_text.strip()
            primary_suspect_type = ps_type
            suspect_source = "elicited"
        awaiting_suspect_answer = False
        # A suspect answer often smuggles in real observations ("the state is open
        # but the rule shows no sign of being triggered" — session 3535204e, where
        # the very next move asked for exactly that value and earned an "I just
        # told you"). Record it as evidence so the evaluator can see it.
        if user_text.strip():
            evidence_list = list(tool_updates.get("evidence", []) or [])
            evidence_list.append(
                {
                    "source": "user",
                    "content": f"User's stated suspicion (verbatim): {user_text}",
                    "status": "reported",
                }
            )
            tool_updates["evidence"] = _deduplicate_dicts(evidence_list)
    elif awaiting_prior_actions:
        # The participant is answering the "have you tried anything?" follow-up.
        prior_actions = user_text.strip()
        awaiting_prior_actions = False
        prior_actions_asked = True
        # Same as the suspect answer: what they tried is evidence, not just metadata.
        if prior_actions:
            evidence_list = list(tool_updates.get("evidence", []) or [])
            evidence_list.append(
                {
                    "source": "user",
                    "content": f"User's prior actions (verbatim): {prior_actions}",
                    "status": "reported",
                }
            )
            tool_updates["evidence"] = _deduplicate_dicts(evidence_list)
    elif not primary_suspect_label:
        # Read a suspect the participant volunteered in their own words.
        ps_type, ps_label = _classify_primary_suspect(user_text)
        if ps_type != "unknown":
            primary_suspect_type = ps_type
            primary_suspect_label = ps_label
            suspect_source = "volunteered"

    # Prior actions, read the same passive way. With the elicitation questions switched off,
    # nothing asks "have you tried anything?" any more — but a participant who HAS tried
    # something says so unprompted while describing the problem, so read it from their own
    # words instead of spending a turn on the question. Bounded to their opening messages:
    # the same first-person phrasing later ("I pressed the button, nothing happened") is a
    # CHECK RESULT, and recording that as something they tried before asking for help would
    # misreport the study variable.
    if (
        not prior_actions
        and not awaiting_prior_actions
        and _user_message_count(state) <= 2
        and _PRIOR_ACTIONS_RE.search(user_text)
    ):
        prior_actions = user_text.strip()

    # Ask for the participant's suspect once, at the very start, before any
    # diagnostic move — but only when the policy is on and the start is genuine
    # (no device locked in, no tool run yet).
    # Only elicit on a genuine problem-description turn. Info/portal-navigation
    # turns reach solve too but must not trigger the mind-map question; location
    # and greeting turns never reach this node.
    elicitable_intent = state.get("turn_intent", "symptom") not in {"info", "portal"}

    # Gate on "has any DIAGNOSTIC move been made yet", not on "do we know which device this
    # is about". Those are different questions, and conflating them broke the condition
    # outright: a participant whose first message was "Where is the smart fan?" got a
    # location lookup, which set target_device_id and pushed a tool into checked_tools — so
    # when they then described the symptom, both gates were already tripped and the suspect
    # question could never fire for the rest of the session. Simply asking where something is
    # silently opted them out of a study condition.
    #
    # Knowing the device is fine — the question is "what's your hunch about the CAUSE", and
    # it makes just as much sense once we know we are talking about the fan. What must not
    # happen is asking it after we have already started checking things.
    diagnosis_started = bool(
        state.get("deduction_timeline")
        or (state.get("last_check_requested") or "").strip()
    )

    should_ask_suspect = (
        elicitation_enabled
        and elicitable_intent
        and not suspect_elicitation_done
        and not awaiting_suspect_answer
        and not diagnosis_started
    )
    if ask_only_if_none:
        should_ask_suspect = should_ask_suspect and not primary_suspect_label

    if should_ask_suspect:
        # This is a CANNED TEMPLATE, emitted before any model runs — so it cannot adapt to
        # what the participant wrote unless this code adapts it. The default wording ends in
        # "have you tried anything yourself yet?", which lands badly when their message
        # ALREADY says what they did ("I closed the window but the fan didn't start"): it
        # reads as not listening, and pilot participants said so. When their message
        # describes actions, use the variant without that tail, and keep their narrative as
        # prior_actions so downstream turns know what was tried.
        described_actions = bool(_PRIOR_ACTIONS_RE.search(user_text))
        key = "question_text_when_actions_described" if described_actions else "question_text"
        question = " ".join(
            str(elicitation_policy.get(key) or elicitation_policy.get("question_text", "")).split()
        ) or _DEFAULT_SUSPECT_QUESTION
        if described_actions and not prior_actions:
            prior_actions = user_text.strip()
        return {
            "messages": [{"role": "assistant", "content": question}],
            "reported_symptom": reported_symptom,
            "diagnosis_phase": "suspect_elicitation",
            "pending_question": question,
            "next_action_type": "ask_clarification",
            "last_agent_reply": question,
            "reply_options": [],
            "suspect_elicitation_done": True,
            "awaiting_suspect_answer": True,
            "deduction_timeline": deduction_timeline,
            "primary_suspect_label": primary_suspect_label,
            "primary_suspect_type": primary_suspect_type,
            "suspect_source": suspect_source,
            "prior_actions": prior_actions,
            **obs_updates,
            **tool_updates,
        }

    # Separate one-line follow-up: "have you tried anything so far?" — asked once,
    # after the suspect step, before any diagnostic move. Same start guards.
    should_ask_prior_actions = (
        elicitation_enabled
        and bool(elicitation_policy.get("ask_prior_actions", False))
        and elicitable_intent
        and not prior_actions_asked
        and not awaiting_prior_actions
        and not awaiting_suspect_answer
        and not state.get("target_device_id")
        and not state.get("checked_tools")
    )

    if should_ask_prior_actions:
        question = " ".join(
            str(elicitation_policy.get("prior_actions_question", "")).split()
        ) or _DEFAULT_PRIOR_ACTIONS_QUESTION
        return {
            "messages": [{"role": "assistant", "content": question}],
            "reported_symptom": reported_symptom,
            "diagnosis_phase": "prior_actions",
            "pending_question": question,
            "next_action_type": "ask_clarification",
            "last_agent_reply": question,
            "reply_options": [],
            "prior_actions_asked": True,
            "awaiting_prior_actions": True,
            "deduction_timeline": deduction_timeline,
            "prior_actions": prior_actions,
            "primary_suspect_label": primary_suspect_label,
            "primary_suspect_type": primary_suspect_type,
            "suspect_source": suspect_source,
            "suspect_elicitation_done": suspect_elicitation_done,
            "awaiting_suspect_answer": awaiting_suspect_answer,
            **obs_updates,
            **tool_updates,
        }

    target_device_id = state.get("target_device_id", "")
    target_device = state.get("target_device", "")
    target_device_type = state.get("target_device_type", "")
    ambiguous_candidates = state.get("ambiguous_device_candidates", []) or []

    if not target_device_id:
        resolved_id, resolved_name, resolved_type, candidates = _resolve_device_from_text(
            config,
            user_text,
        )

        if resolved_id:
            target_device_id = resolved_id
            target_device = resolved_name
            target_device_type = resolved_type
            ambiguous_candidates = []
        elif candidates:
            ambiguous_candidates = candidates

    if ambiguous_candidates and not target_device_id:
        # If we ALREADY asked this disambiguation last turn and still couldn't resolve, do not
        # ask again verbatim — the participant pushed back ("I already described it to you") and
        # a repeated identical question is what killed pilot session 4bc1a5cc. Commit to the
        # most likely candidate instead, scored against everything they have said, and move on.
        already_asked = (state.get("pending_question") or "").startswith("Which one do you mean")
        if already_asked:
            resolution_text = f"{reported_symptom} {user_text}".strip()
            committed = _pick_best_candidate(config, ambiguous_candidates, resolution_text)
            committed_device = find_device(config["environment"], committed) if committed else None
            if committed_device:
                target_device_id = str(committed_device.get("id", ""))
                target_device = str(committed_device.get("name", ""))
                target_device_type = str(committed_device.get("type", ""))
                ambiguous_candidates = []

    if ambiguous_candidates and not target_device_id:
        labels = _candidate_labels(config, ambiguous_candidates)
        question = "Which one do you mean: " + " or ".join(labels) + "?"

        return {
            "messages": [{"role": "assistant", "content": question}],
            "reported_symptom": reported_symptom,
            "diagnosis_phase": "entity_resolution",
            "pending_question": question,
            "next_action_type": "ask_clarification",
            # Disambiguation has a real closed answer set: the candidates themselves.
            "reply_options": _normalize_reply_options(labels),
            "ambiguous_device_candidates": ambiguous_candidates,
            "deduction_timeline": deduction_timeline,
            "primary_suspect_label": primary_suspect_label,
            "primary_suspect_type": primary_suspect_type,
            "suspect_source": suspect_source,
            "suspect_elicitation_done": suspect_elicitation_done,
            "awaiting_suspect_answer": awaiting_suspect_answer,
            "prior_actions": prior_actions,
            "prior_actions_asked": prior_actions_asked,
            "awaiting_prior_actions": awaiting_prior_actions,
            **obs_updates,
            **tool_updates,
        }

    enriched_state = {
        **state,
        **tool_updates,
        **obs_updates,
        "reported_symptom": reported_symptom,
        "primary_suspect_label": primary_suspect_label,
        "primary_suspect_type": primary_suspect_type,
        "suspect_source": suspect_source,
        "target_device_id": target_device_id,
        "target_device": target_device,
        "target_device_type": target_device_type,
    }

    stable_prompt, volatile_prompt = build_runtime_system_prompt(
        config, enriched_state, split=True
    )
    available_devices = list_environment_devices(config["environment"])

    tool_instruction = f"""
Available devices:
{json.dumps(available_devices, ensure_ascii=False, indent=2)}

Tool-use rules:
- Use get_device_info when a device is identified and properties are needed.
- Use get_device_knowledge when you need to explain how the device works.
- Use check_device_connectivity when connection, Wi-Fi, Zigbee, hub, or command delivery may matter.
- Use check_hub_status when several devices fail or the target device depends on the hub.
- Use show_device_location when the user needs to physically find or inspect a device.
- Use get_portal_entity_info when the next evidence should come from the dashboard state/entity.
- Use get_device_portal_check_guide when you need to guide the user to check a device's State, Last changed, or an unavailable/offline indicator in the dashboard.
- Use get_portal_task_guide when you need to guide the user through All Rules, All Devices, or (floor-map condition only) the Connections / dependency graphs.
- Use exactly the device id when possible, for example: doorlight.
"""

    response_instruction = """
At this turn, do exactly ONE diagnostic move:

Option A:
Call one or more tools only if they support one single diagnostic check.

Option B:
Ask the user to perform one safe physical check.

Option C:
Guide the user to one exact dashboard location and ask for one set of values.

Option D:
Give one concise conclusion only if enough evidence exists.

Visual output rules:
- Never paste raw image paths, filenames, absolute paths, or URLs into the reply text.
- If a visual tool was used, say something natural like:
  "I marked it on the floorplan below."
- The frontend will render the actual image from visual_outputs.
- Do not write the image path in markdown.

Do not give a long plan.
Do not list all possible causes.
Do not say the device is broken without evidence.
Do not state a device's physical position — which side it is on, which window or wall it is by,
that it is "behind" or "above" something — unless a tool result's location_hint or the participant
said so. If you have not been told the side, do not guess it: say "its own control on the unit"
rather than inventing "on the right". A wrong location sends the participant to the wrong place.
Stop after the one move and wait for the user's result.
"""

    # When the user just answered the previous check, tell the model so it
    # acknowledges the result and advances instead of regenerating the same
    # request (which temperature=0 makes byte-identical on an unchanged prompt).
    answer_ack_instruction = ""
    if answered_previous_check:
        answer_ack_instruction = (
            "\n\nThe user's latest message is their answer to your previous request"
            f' ("{outstanding_check[:200]}"). Treat that request as answered:'
            " briefly acknowledge what they reported, then give the NEXT distinct"
            " diagnostic move. Never repeat your previous request verbatim. If they"
            " answered only part of it, ask only for the specific missing detail."
        )

    # Revise retry: the reviewer rejected the previous move THIS turn and routed us back here.
    # The feedback was being written to state and never read — so the retry re-ran on an
    # unchanged prompt and (at temperature 0) reproduced the rejected move, which is why a
    # rejection marched straight to a block and the agent looped on a fallback. Inject the
    # feedback as an imperative so the retry actually produces a DIFFERENT move.
    revision_instruction = ""
    if int(state.get("revision_count", 0) or 0) > 0 and state.get("evaluator_feedback"):
        revision_instruction = (
            "\n\nSTOP — your previous move this turn was REJECTED by the internal reviewer and "
            "was NOT sent to the user. Reason:\n"
            f"  {state.get('evaluator_feedback')}\n"
            "Do NOT repeat that move or restate that conclusion. Fix the stated problem: if the "
            "reason says you have not yet run the check that separates the fault types, then DO "
            "NOT CONCLUDE this turn — instead ask the user for exactly that one check. Produce a "
            "different, concrete next move that resolves the reviewer's objection."
        )

    # The participant explicitly asked what KIND of fault it is. A flat "we can't label it
    # yet" reads as stonewalling and burned a pilot session (4c21d25f). If the discriminating
    # evidence is not yet on record, don't refuse — answer with your current lean, marked
    # provisional, and ask for the ONE check that confirms it. This deliberately relaxes the
    # "never name the categories" rule for this one case: they asked a direct question, so
    # answer it. Still no outcome-mapping — name the lean, never what the check would prove.
    label_ask_instruction = ""
    if _asked_for_fault_label(user_text):
        label_ask_instruction = (
            "\n\nThe user is directly asking what TYPE of fault this is (device / connection /"
            " configuration). Answer their question: state your current single most likely"
            " category in plain words, MARKED PROVISIONAL (e.g. \"it's looking most like a"
            " connection problem\"). If you already have the discriminating evidence, conclude"
            " with that label now. If you do NOT yet, still name the provisional lean, then ask"
            " for the ONE check that would confirm it — do NOT reply that you cannot label it"
            " yet with no answer at all. Never say what the check's result would prove."
        )

    # Assemble STABLE-FIRST, VOLATILE-LAST so the ~10K stable prefix (rules + tool list +
    # response instructions [+ single-call move guidance]) is byte-identical across turns and
    # gets prefix-cached; only the tail (conversation state + answer-ack + revision) varies.
    stable_prefix = f"{stable_prompt}\n\n{tool_instruction}\n\n{response_instruction}"
    if _SINGLE_CALL_MOVE:
        # DiagnosticMove bound as a submit-tool -> the structured move comes back in the SAME
        # call the model decides not to gather more info (no second full pass).
        stable_prefix = f"{stable_prefix}{_MOVE_TOOL_GUIDANCE}"
        model = init_chat_model(MODEL_NAME, temperature=0).bind_tools([*TOOLS, DiagnosticMove])
    else:
        model = init_chat_model(MODEL_NAME, temperature=0).bind_tools(TOOLS)
    volatile_suffix = (
        f"{volatile_prompt}{answer_ack_instruction}{revision_instruction}{label_ask_instruction}"
    )
    system_content = f"{stable_prefix}\n\n{volatile_suffix}"

    response = model.invoke(
        [
            {"role": "system", "content": system_content},
            *messages,
        ]
    )

    # Responses-API content is a list of blocks (reasoning + text); extract just the text.
    content = content_to_text(getattr(response, "content", "")).strip()
    # Split info-gathering tool calls from a DiagnosticMove submit call. When the flag is off,
    # DiagnosticMove is never bound, so submit_call is always None and info_calls == all calls
    # — identical to the previous `has_tool_calls = bool(response.tool_calls)`.
    _all_calls = list(getattr(response, "tool_calls", None) or [])
    _info_calls = [tc for tc in _all_calls if _tool_call_name(tc) != _SUBMIT_MOVE_NAME]
    _submit_call = next((tc for tc in _all_calls if _tool_call_name(tc) == _SUBMIT_MOVE_NAME), None)
    # Gather info first if the model asked for it, even if it also (prematurely) submitted.
    has_tool_calls = bool(_info_calls)

    pending_question = ""
    # A check the user has now answered is consumed this turn; don't carry the
    # stale instruction forward or the next prompt looks unchanged again.
    last_check_requested = "" if answered_previous_check else state.get("last_check_requested", "")
    next_action_type = state.get("next_action_type", "none")
    issue_identified = bool(state.get("issue_identified", False))
    root_cause = state.get("root_cause", "") or ""
    recommended_action = state.get("recommended_action", "") or ""
    fault_label = state.get("fault_label", "") or ""
    diagnosis_phase = "guided_diagnosis"

    working_hypothesis = state.get("working_hypothesis", "") or ""

    # Quick-reply buttons for this turn. Always rebuilt from the current move —
    # never carried over — so buttons can't outlive the question they answer.
    reply_options: list[str] = []

    # Ranked hypothesis board (Phase 10). Carried over untouched on a tool-loop
    # turn; replaced from the move on a real reply turn.
    hypotheses_board: list[dict[str, Any]] = list(state.get("hypotheses", []) or [])
    rejected_board: list[dict[str, Any]] = list(state.get("rejected_hypotheses", []) or [])

    # Per-move, never carried across turns: it describes what THIS reply asks the user to
    # touch. A stale value would point the affordance check at last turn's device.
    operated_device_id = ""

    if has_tool_calls:
        # Stage 1: the model still wants tools — let the tool loop run (unchanged).
        next_action_type = "tool_check"
        diagnosis_phase = "guided_diagnosis"
        # Strip any co-emitted submit call so ToolNode never tries to execute the
        # non-executable DiagnosticMove tool. When nothing was stripped, pass the
        # original response through unchanged (identical to the two-call path).
        outgoing_message = (
            response if _submit_call is None
            else AIMessage(content=content, tool_calls=_info_calls)
        )
        agent_reply = content
    else:
        # Stage 2: no more info to gather. Get the structured DiagnosticMove — from the
        # submit-tool call the model just made (single-call path, no extra transaction), or
        # via a dedicated structured call (default two-call path / degenerate no-tool reply).
        if _submit_call is not None:
            move = _move_from_tool_args(_tool_call_args(_submit_call), fallback_text=content)
        else:
            move = _generate_diagnostic_move(
                system_content,
                messages,
                previous_assistant=_previous_assistant_text(messages),
                fallback_text=content,
            )
        agent_reply = (move.reply_text or content or "").strip()
        # A check must never reach the participant as a bare order. See _ensure_purpose.
        agent_reply = _ensure_purpose(agent_reply, move.rationale, move.action_type)
        # Keep the last non-empty hypothesis: a turn that produces none is a turn where the
        # hypothesis stands, not one where the agent forgot it had one.
        working_hypothesis = (move.working_hypothesis or "").strip() or working_hypothesis
        hypotheses_board, rejected_board = _merge_hypotheses(state, move.hypotheses)
        outgoing_message = AIMessage(content=agent_reply)

        action_type = move.action_type
        next_action_type = _MOVE_TO_NEXT_ACTION.get(action_type, "explain")

        # Prefer the device the move explicitly named (structured entity resolution).
        if move.target_device_id:
            resolved = find_device(config["environment"], move.target_device_id)
            if resolved:
                target_device_id = str(resolved.get("id", "")) or target_device_id
                target_device = str(resolved.get("name", "")) or target_device
                target_device_type = str(resolved.get("type", "")) or target_device_type

        # Resolve the hands-on device the same way, but keep it separate — it is an input to
        # the affordance check, not the subject of the diagnosis, so it must never overwrite
        # target_device_* or the case file would credit the check to the wrong device.
        if move.operated_device_id:
            operated = find_device(config["environment"], move.operated_device_id)
            if operated:
                operated_device_id = str(operated.get("id", "")) or ""

        # Buttons appear only under checks — the one move kind whose answer can
        # have a valid closed set of expected readings. Everything else (open
        # clarifications, explanations, conclusions) stays free-text only.
        if action_type in {"physical_check", "portal_check"}:
            reply_options = _normalize_reply_options(move.reply_options)

        if action_type == "ask_clarification":
            pending_question = agent_reply
        elif action_type == "physical_check":
            last_check_requested = agent_reply
            diagnosis_phase = "physical_check"
        elif action_type == "portal_check":
            last_check_requested = agent_reply
            diagnosis_phase = "portal_check"
        elif action_type in {"conclude", "escalate"}:
            # Terminal diagnoses: record the cause + fault classification so the
            # case-file panel and logs can show them (Root Cause #10 fix).
            issue_identified = True
            diagnosis_phase = (
                "escalation" if action_type == "escalate" else "issue_identification"
            )
            if action_type == "escalate":
                recommended_action = "Contact a technician."
            if move.fault_label:
                fault_label = move.fault_label

            # Lock-on-first: keep the first high-probability cause through the rest
            # of the chat; only replace it when the agent explicitly revises it.
            if not root_cause:
                root_cause = agent_reply
            elif _is_revised_conclusion(agent_reply) and agent_reply.strip() != root_cause.strip():
                root_cause = agent_reply
        else:  # explain
            last_check_requested = agent_reply

        # Open a timeline step for non-terminal moves (closed next turn when the
        # participant reports back). Replace an already-open step rather than stack.
        if action_type not in {"conclude", "escalate"}:
            new_step = {
                "suspect": _hypothesis_label(
                    agent_reply,
                    target_device,
                    primary_suspect_label,
                    primary_suspect_type,
                    environment=config["environment"],
                ),
                "action": _clean_action_text(agent_reply),
                "result": "",
                "action_type": next_action_type,
                "target_device_id": target_device_id,
                # The closed set offered with this check (empty for open questions). Persisted so
                # the fault-type gate can read the participant's answer STRUCTURALLY — a negative
                # pick on a "did the dashboard update?" check is an unambiguous system-blind signal,
                # instead of re-parsing free prose (which looped in session 09efba4b).
                "offered_options": list(reply_options),
            }
            if deduction_timeline and not deduction_timeline[-1].get("result"):
                deduction_timeline[-1] = new_step
            else:
                deduction_timeline.append(new_step)

    return {
        "messages": [outgoing_message],
        "reported_symptom": reported_symptom,
        "deduction_timeline": deduction_timeline,
        "diagnosis_phase": diagnosis_phase,
        "target_space": config["environment"].get("space", {}).get("name", "Smart Home"),
        "target_device": target_device,
        "target_device_id": target_device_id,
        "target_device_type": target_device_type,
        "operated_device_id": operated_device_id,
        "ambiguous_device_candidates": ambiguous_candidates,
        "pending_question": pending_question,
        "last_check_requested": last_check_requested,
        "next_action_type": next_action_type,
        "reply_options": reply_options,
        "issue_identified": issue_identified,
        "last_agent_reply": agent_reply,
        "primary_suspect_label": primary_suspect_label,
        "primary_suspect_type": primary_suspect_type,
        "suspect_source": suspect_source,
        "suspect_elicitation_done": suspect_elicitation_done,
        "awaiting_suspect_answer": awaiting_suspect_answer,
        "prior_actions": prior_actions,
        "prior_actions_asked": prior_actions_asked,
        "awaiting_prior_actions": awaiting_prior_actions,
        "root_cause": root_cause,
        "recommended_action": recommended_action,
        "fault_label": fault_label,
        "working_hypothesis": working_hypothesis,
        "hypotheses": hypotheses_board,
        "rejected_hypotheses": rejected_board,
        "diagnostic_checklist": _update_diagnostic_checklist(
            state, reported_symptom, fault_label=fault_label
        ),
        **obs_updates,
        **tool_updates,
    }


_REVISION_CUES = (
    "actually", "turns out", "on closer look", "on a closer look", "correction",
    "i was wrong", "the real cause", "real issue", "now it looks", "revised",
    "scratch that", "in fact the cause", "the actual cause",
)


def _is_revised_conclusion(text: str) -> bool:
    """True when a later verdict explicitly overrides an earlier one.

    A locked root cause is only replaced when the agent signals it has changed
    its mind (e.g. "actually, the real cause is ..."), so ordinary continued
    troubleshooting can't clobber a high-confidence cause already found.
    """
    t = text.lower()
    return any(cue in t for cue in _REVISION_CUES)

# ══════════════════════════════════════════════════════════════════════════════════════
# Evaluator node (Phase 8)
# ══════════════════════════════════════════════════════════════════════════════════════

# Human phrasing for the three fault labels, used only to voice a PROVISIONAL lean when the
# participant has explicitly demanded the error type before the discriminating check is on
# record. Naming the category early softens the "don't name the categories you are deciding
# between" rule (config_loader) — done deliberately and ONLY on an explicit ask, because the
# pilots showed a flat "we can't label it yet" reads as stonewalling and burned the session
# (4c21d25f: "You are stupid… What type of the error we have?").
_FAULT_LABEL_PHRASE = {
    "device_error": "a problem with the device itself",
    "connection_error": "a connection problem",
    "configuration_error": "a rule/configuration problem",
}

# The participant asking, in their own words, what KIND of fault this is. Deliberately narrow:
# it must be a request for the classification, not just any message containing "error".
_ASKED_FOR_LABEL_RE = re.compile(
    r"\b(what|which|what'?s)\b[^?]*\b(type|kind|sort)\b[^?]*\b(error|problem|issue|fault)\b"
    r"|\b(error|problem|issue|fault)\b[^?]*\b(type|kind|sort)\b"
    r"|\bwhat'?s\s+(the\s+)?(error|problem|issue|fault)\b",
    re.IGNORECASE,
)


def _asked_for_fault_label(text: str) -> bool:
    return bool(_ASKED_FOR_LABEL_RE.search(text or ""))


def _fault_type_recovery_message(
    device: dict[str, Any], provisional_label: str = ""
) -> str:
    """Participant-facing physical check that separates device from connection, when a
    conclusion was blocked for want of it.

    `provisional_label` is set ONLY when the participant explicitly asked what type of error
    it is: then we answer their question with the current lean, marked provisional, and still
    ask for the one check that confirms it — instead of a bare "we can't label it yet", which
    the pilots showed reads as a refusal. When it is empty we give the check alone, with no
    category named (the default non-biasing behaviour). Either way: no outcome-mapping — we
    never tell them what the check's result would prove."""
    name = device.get("name", "the device")
    if str(device.get("type", "")).lower() == "sensor":
        check = (
            f"Go to the {name}, and as you open and close it by hand, watch the small "
            f"indicator light on the sensor itself. Does that light come on when you do?"
        )
    else:
        check = (
            f"Try operating the {name} at its own buttons or switch on the unit, and tell me "
            f"whether it actually responds."
        )

    lean = _FAULT_LABEL_PHRASE.get(provisional_label, "")
    if lean:
        return (
            f"It's looking most like {lean} — but I want one check to be sure before I call "
            f"it. {check}"
        )
    return f"Before we call it — one quick thing in the room. {check}"


def evaluate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Audit the move solve_node just made, before the participant sees it.

    Runs only when solve_node has finished calling tools and produced a reply. The reply is
    already in `messages`, but the API surfaces `last_agent_reply` — so a revision that
    overwrites it means the participant never sees the rejected version.

    EVERY verdict is logged, approvals included. That log is the measurement: a real count of
    duplicates, impossible actions, premature conclusions and ungrounded claims, rather than
    one reconstructed by reading transcripts afterwards.
    """
    config = _get_config_for_scenario(state.get("active_scenario_id"))

    # Rebuild the move from what solve_node put in state. _MOVE_TO_NEXT_ACTION is the identity,
    # so next_action_type is the action_type — no extra plumbing needed.
    class _Move:
        action_type = str(state.get("next_action_type", "") or "")
        target_device_id = str(state.get("target_device_id", "") or "")
        operated_device_id = str(state.get("operated_device_id", "") or "")
        reply_text = str(state.get("last_agent_reply", "") or "")
        fault_label = state.get("fault_label") or None

    move = _Move()

    # Nothing to audit: no reply, or the model is still mid-tool-loop.
    if not move.reply_text or move.action_type in ("", "tool_check"):
        return {"evaluator_verdict": "approved"}

    verdict = evaluate_move(move, state, config)

    log = list(state.get("evaluator_log", []) or [])
    log.append({
        "turn": len(log) + 1,
        "verdict": verdict.verdict,
        "checks_failed": verdict.checks_failed,
        "feedback": verdict.feedback or "",
        "action_type": move.action_type,
        "target_device_id": move.target_device_id,
        # Logged so an affordance veto can be audited afterwards: without it, a rejection
        # naming a device the reply never told anyone to touch is unreadable in the log.
        "operated_device_id": move.operated_device_id,
        "reply_text": move.reply_text[:400],
        # True => the groundedness judge could not run this turn, so this record's "approved"
        # means UNJUDGED, not audited-and-clean. Counting these separates a genuinely clean
        # session from one where the judge was silently down (quota, credentials, timeout).
        "groundedness_unavailable": verdict.groundedness_unavailable,
    })

    updates: dict[str, Any] = {
        "evaluator_verdict": verdict.verdict,
        "evaluator_feedback": verdict.feedback or "",
        "evaluator_log": log,
    }

    if verdict.verdict in {"revise", "block"}:
        # Un-write everything the rejected move wrote into state BEFORE this audit ran.
        # The participant never saw the move, so no state may claim they did. Each guard
        # compares against the rejected reply so an earlier APPROVED move is never touched.
        #
        #  - root_cause & co: a rejected conclusion must not stay locked in the case file
        #    (session 3535204e: case_file.root_cause was the evaluator-rejected sentence).
        if move.action_type in {"conclude", "escalate"} and (
            (state.get("root_cause") or "").strip() == move.reply_text.strip()
        ):
            updates["root_cause"] = ""
            updates["issue_identified"] = False
            updates["fault_label"] = ""
            updates["recommended_action"] = ""
        #  - the open timeline step: otherwise the case-file rail shows the rejected check
        #    as sent and "Awaiting your reply…" (session 6b92ead4 — the rail showed a Door
        #    Sensor check the chat never delivered), and on a revise retry solve_node would
        #    close that ghost step with the user's PREVIOUS message as its "result".
        timeline = list(state.get("deduction_timeline", []) or [])
        if (
            timeline
            and isinstance(timeline[-1], dict)
            and not str(timeline[-1].get("result", "")).strip()
            and timeline[-1].get("action") == _clean_action_text(move.reply_text)
        ):
            updates["deduction_timeline"] = timeline[:-1]
        #  - the outstanding-check pointer: if it still names the rejected instruction, the
        #    next user message would be captured as the answer to a question never asked.
        if (state.get("last_check_requested") or "").strip() == move.reply_text.strip():
            updates["last_check_requested"] = ""
        if (state.get("pending_question") or "").strip() == move.reply_text.strip():
            updates["pending_question"] = ""
        #  - and the draft message itself. Un-writing the derived state above achieves nothing
        #    while the rejected text is still sitting in `messages`: agent.py dumps the whole
        #    list to logs/chats, so every rejection reached the participant and the graders.
        #    Session 13fa79f9 shipped four rejected drafts — the participant read the same
        #    unplug-the-fan instruction in four wordings, and eval/session_metrics counted
        #    them as real agent turns.
        rejected_id = _rejected_draft_id(state, move.reply_text)
        if rejected_id:
            updates["messages"] = [RemoveMessage(id=rejected_id)]

    if verdict.verdict == "revise":
        updates["revision_count"] = int(state.get("revision_count", 0) or 0) + 1
        return updates

    if verdict.verdict == "block":
        # Second failure. Do NOT loop — a participant is sitting in the lab. Hand control back
        # to them rather than shipping a move we could not vouch for, and do not invent a new
        # claim (inventing one is what got us here).
        #
        # BUT if the block is because the device-vs-connection type was never discriminated,
        # a generic "take stock" fallback just invites the same conclusion again next turn
        # (the loop the simulated eval caught). Instead, hand the participant the ONE physical
        # check that resolves it — a concrete move, not a shrug.
        fault_type_fail = any(
            str(c).startswith("fault_type") for c in (verdict.checks_failed or [])
        )
        device = (
            find_device(config["environment"], move.target_device_id)
            if getattr(move, "target_device_id", "")
            else None
        )
        if fault_type_fail and device:
            # If the participant explicitly demanded the error type, answer with the blocked
            # move's own lean, marked provisional — otherwise give the check with no category
            # named. move.fault_label is the classification the gate just refused to RECORD;
            # voicing it as provisional does not record it (the gate still governs the case
            # file), it only stops the refusal from reading as a stonewall.
            provisional = (
                str(move.fault_label or "")
                if _asked_for_fault_label(_latest_user_text(state))
                else ""
            )
            recovery = _fault_type_recovery_message(device, provisional_label=provisional)
            # Append, never overwrite: updates["messages"] already holds the RemoveMessage
            # that retracts the blocked draft, and dropping it would put the draft back.
            updates["messages"] = [*updates.get("messages", []), AIMessage(content=recovery)]
            updates["last_agent_reply"] = recovery
            updates["next_action_type"] = "physical_check"
            updates["last_check_requested"] = recovery
            updates["pending_question"] = ""
            updates["reply_options"] = []
            updates["revision_count"] = 0
            return updates

        updates["messages"] = [*updates.get("messages", []), AIMessage(content=BLOCK_FALLBACK)]
        updates["last_agent_reply"] = BLOCK_FALLBACK
        updates["next_action_type"] = "ask_clarification"
        updates["pending_question"] = BLOCK_FALLBACK
        # The blocked move's buttons would answer a question that was never sent.
        updates["reply_options"] = []
        # And the retry budget must not leak into the NEXT turn: without this reset the
        # counter stays maxed after a block, so every later turn's FIRST imperfection
        # became an instant block — the fallback repeating verbatim, turn after turn
        # (session 6b92ead4 showed it twice in a row).
        updates["revision_count"] = 0
        return updates

    # approved — clear the retry budget for the next turn
    updates["revision_count"] = 0
    updates["evaluator_feedback"] = ""
    return updates
