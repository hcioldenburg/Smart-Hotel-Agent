from __future__ import annotations

"""
Simple router for the smart-home troubleshooting agent.

It only decides whether the next step should be clarification or diagnosis.
The deeper reasoning happens in nodes.py.
"""

import os
import re
from typing import Any


INFO_KEYWORDS = {
    "list devices",
    "what devices",
    "which devices",
    "available devices",
    "show devices",
    "space",
    "studio",
    "smart home",
}

# Phrases that, on their own, mean the user wants to FIND/SEE a device.
LOCATION_KEYWORDS = {
    "where is",
    "where's",
    "where can i find",
    "locate",
    "location of",
    "find the",
    "on the map",
    "on the floor plan",
    "on the floorplan",
    "floorplan",
    "floor plan",
}

# Generic verbs that only mean "locate" when paired with a device reference
# (e.g. "show me the fan" -> locate, but "show me the rules" -> not locate).
LOCATION_VERBS = {"show me", "map"}

# Device-ish words used to disambiguate generic locate verbs.
DEVICE_HINT_WORDS = {
    "lamp", "light", "bulb", "thermostat", "heater", "sensor", "blind",
    "blinds", "curtain", "shutter", "roller", "tablet", "plug", "socket",
    "switch", "button", "fan", "tv", "hub", "device", "presence", "motion",
    "door", "window", "temperature", "humidity", "bedlight", "bedside",
    "floor lamp", "wall light", "ir", "remote",
}

PORTAL_KEYWORDS = {
    "portal",
    "app",
    "tablet",
    "interface",
    "dashboard",
    "rules",
    "rule",
    "automation",
    "condition",
    "conditions",
    "trigger",
    "triggers",
    "action",
    "actions",
    "state",
    "status",
    "parameters",
    "connected devices",
    "all devices",
    "your room",
    "network",
    "dependencies",
    "connections",
}

TROUBLESHOOTING_KEYWORDS = {
    "not working",
    "doesn't work",
    "does not work",
    "won't",
    "cannot",
    "can't",
    "unresponsive",
    "not responding",
    "offline",
    "broken",
    "stuck",
    "failed",
    "issue",
    "problem",
    "wrong",
    "does not turn on",
    "doesn't turn on",
    "not turning on",
    "does not respond",
    "doesn't respond",
    "command",
    "sent",
    "nothing happens",
    "should",
    "supposed to",
    "too warm",
    "too cold",
    "heating",
    "light",
    "lamp",
    "blind",
    "curtain",
    "shutter",
    "roller shutter",
    "thermostat",
    "sensor",
    "tablet",
    "hub",
}

GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "howdy",
    "sup",
    "greetings",
}

FOLLOWUP_KEYWORDS = {
    "yes",
    "no",
    "done",
    "it worked",
    "it didn't",
    "it did not",
    "still not",
    "same",
    "nothing",
    "now",
    "i checked",
    "checked",
    "it is",
    "it isn't",
    "it is not",
    "online",
    "offline",
    "on",
    "off",
    "responds",
    "doesn't respond",
    "does not respond",
    "open",
    "closed",
    "unavailable",
    "unknown",
}


_GREETING_LEAD_RE = re.compile(
    r"^\s*(hi|hello|hey|hiya|howdy|yo|greetings|good\s+(morning|afternoon|evening))\b[\s,!.:-]*",
    re.IGNORECASE,
)
_NAME_LEAD_RE = re.compile(r"^\s*sherlock\b[\s,!.:-]*", re.IGNORECASE)
_POSSESSIVE_LEAD_RE = re.compile(r"^\s*(my|the)\s+", re.IGNORECASE)


def normalize_symptom(text: str) -> str:
    """Turn a raw first message into a clean symptom label for the case file.

    Strips a leading greeting and the assistant's name (in either order) plus a
    leading "my"/"the", so "hello sherlock my fan is not working" becomes
    "Fan is not working". Falls back to the trimmed original if stripping would
    empty it.
    """
    t = " ".join(str(text).split())
    if not t:
        return ""

    # Greeting and name can appear in either order ("hi sherlock" / "sherlock hi").
    for _ in range(2):
        t = _GREETING_LEAD_RE.sub("", t)
        t = _NAME_LEAD_RE.sub("", t)

    stripped = _POSSESSIVE_LEAD_RE.sub("", t).strip()
    t = (stripped or t).strip()

    if not t:
        return " ".join(str(text).split())

    return t[:1].upper() + t[1:]


def _get_last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", "")).strip()

        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role in ("human", "user"):
            return str(getattr(msg, "content", "")).strip()

    return ""


def _contains_any(text: str, keywords: set[str]) -> bool:
    t = text.lower().strip()
    return any(keyword in t for keyword in keywords)


def _is_too_vague(text: str) -> bool:
    t = text.lower().strip()

    if not t:
        return True

    vague_exact = {
        "help",
        "hello",
        "hi",
        "hey",
        "problem",
        "issue",
        "not working",
        "something is wrong",
        "there is a problem",
    }

    if t in vague_exact:
        return True

    if len(t.split()) <= 2 and not _contains_any(t, INFO_KEYWORDS | PORTAL_KEYWORDS):
        return True

    return False


def _is_greeting(text: str) -> bool:
    t = text.lower().strip().rstrip("!.,")
    return t in GREETING_WORDS


def _looks_like_location_request(text: str) -> bool:
    t = text.lower().strip()

    if _contains_any(t, LOCATION_KEYWORDS):
        return True

    # generic verb ("show me", "map") only counts as locate with a device hint
    if _contains_any(t, LOCATION_VERBS) and _contains_any(t, DEVICE_HINT_WORDS):
        return True

    return False


def classify_intent(text: str) -> str:
    """
    Classify one user turn into a coarse intent (keyword-first).

    Returns one of: greeting, location_request, info, portal, symptom,
    followup, ambiguous. The router maps this to a graph route; downstream
    nodes (e.g. suspect elicitation) gate on it.

    This is deterministic on purpose so study runs are reproducible. An optional
    small-model fallback (see _llm_classify_intent) refines only the cases this
    returns as "ambiguous", and only when explicitly enabled.
    """
    t = text.lower().strip()

    if not t:
        return "ambiguous"

    if _is_greeting(t):
        return "greeting"

    # Location is checked before generic info so "where is the fan" routes to a
    # photo rather than a device listing.
    if _looks_like_location_request(t):
        return "location_request"

    if _contains_any(t, INFO_KEYWORDS):
        return "info"

    # A problem description wins over a bare portal/navigation mention, so a
    # symptom that happens to name "rules" still triggers diagnosis (and suspect
    # elicitation) instead of being treated as pure navigation.
    if _contains_any(t, TROUBLESHOOTING_KEYWORDS):
        return "symptom"

    if _contains_any(t, PORTAL_KEYWORDS):
        return "portal"

    if _contains_any(t, FOLLOWUP_KEYWORDS):
        return "followup"

    return "ambiguous"


def _llm_classify_intent(text: str) -> str | None:
    """
    Optional small-model fallback for turns the keyword classifier marks
    "ambiguous". Disabled unless SMART_HOME_INTENT_LLM_FALLBACK is truthy, so the
    default routing stays deterministic and free of extra model calls.
    """
    if os.getenv("SMART_HOME_INTENT_LLM_FALLBACK", "").lower() not in {"1", "true", "yes"}:
        return None

    try:
        from langchain.chat_models import init_chat_model

        model_name = os.getenv("SMART_HOME_INTENT_MODEL", "gpt-5.4-mini")
        model = init_chat_model(model_name, temperature=0)
        allowed = "greeting, location_request, info, portal, symptom, followup"
        response = model.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Classify the user's smart-home message into exactly one "
                        f"intent label from: {allowed}. Reply with only the label."
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        label = str(getattr(response, "content", "")).strip().lower()
        valid = {
            "greeting", "location_request", "info",
            "portal", "symptom", "followup",
        }
        return label if label in valid else None
    except Exception:
        return None


def _has_active_diagnosis(state: dict[str, Any]) -> bool:
    portal = state.get("portal_context") or {}
    # default portal_context set in initial_state doesn't count as active
    portal_is_meaningful = bool(
        portal.get("last_portal_instruction")
        or portal.get("last_portal_observation")
        or portal.get("relevant_screen")
    )
    return bool(
        state.get("reported_symptom")
        or state.get("target_device_id")
        or state.get("pending_question")
        or state.get("last_check_requested")
        or state.get("evidence")
        or portal_is_meaningful
    )


def route_node(state: dict[str, Any]) -> dict[str, Any]:
    last_user = _get_last_user_text(state)

    intent = classify_intent(last_user)
    if intent == "ambiguous":
        refined = _llm_classify_intent(last_user)
        if refined:
            intent = refined

    active = _has_active_diagnosis(state)

    # reported_symptom should only be set by an actual problem description, not
    # by greetings, location/info requests, or bare portal navigation.
    is_symptomatic_turn = intent in {"symptom", "followup"} or (active and intent != "greeting")
    reported_symptom = state.get("reported_symptom") or (
        last_user if is_symptomatic_turn else ""
    )

    # Clean, greeting-free version of the symptom for the case-file panel;
    # computed once and kept stable for the rest of the session.
    normalized_symptom = state.get("normalized_symptom") or (
        normalize_symptom(reported_symptom) if reported_symptom else ""
    )

    updates = {
        "turn_intent": intent,
        "reported_symptom": reported_symptom,
        "normalized_symptom": normalized_symptom,
        "evidence": state.get("evidence", []),
        "confirmed_facts": state.get("confirmed_facts", []),
        "hypotheses": state.get("hypotheses", []),
        "rejected_hypotheses": state.get("rejected_hypotheses", []),
        "visual_outputs": state.get("visual_outputs", []),
        "checked_tools": state.get("checked_tools", []),
        "checked_devices": state.get("checked_devices", []),
        "portal_context": state.get("portal_context", {}),
    }

    # Greetings get a simple clarify response regardless of diagnosis state
    if intent == "greeting" and not state.get("reported_symptom"):
        return {
            **updates,
            "mode": "clarify",
            "diagnosis_phase": "symptom_capture",
            "next_action_type": "ask_clarification",
            "is_greeting": True,
        }

    # Location requests go straight to the deterministic photo node — even mid
    # diagnosis ("where's the hub?") — and never trigger suspect elicitation.
    if intent == "location_request":
        return {
            **updates,
            "mode": "locate",
            "diagnosis_phase": "visual_guidance",
            "next_action_type": "visual_guidance",
        }

    # Once a diagnosis is underway, keep solving (the turn is a follow-up answer
    # or a continued symptom); elicitation is gated downstream by turn_intent.
    if active:
        return {**updates, "mode": "solve"}

    if intent == "info":
        return {
            **updates,
            "mode": "solve",
            "diagnosis_phase": "entity_resolution",
        }

    if intent == "portal":
        return {
            **updates,
            "mode": "solve",
            "diagnosis_phase": "portal_check",
            "next_action_type": "portal_check",
        }

    if intent == "symptom":
        if _is_too_vague(last_user):
            return {
                **updates,
                "mode": "clarify",
                "diagnosis_phase": "symptom_capture",
                "next_action_type": "ask_clarification",
            }

        return {
            **updates,
            "mode": "solve",
            "diagnosis_phase": "symptom_capture",
        }

    # followup with no active diagnosis, or anything still ambiguous -> clarify
    return {
        **updates,
        "mode": "clarify",
        "diagnosis_phase": "symptom_capture",
        "next_action_type": "ask_clarification",
    }