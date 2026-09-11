from __future__ import annotations

"""
State definition for the smart-home troubleshooting agent.
"""

from typing import Any, Literal, TypedDict

from langgraph.graph import MessagesState


def content_to_text(content: Any) -> str:
    """Human-readable text from a message's content, whatever shape it takes.

    On the OpenAI Responses API (reasoning models like gpt-5.6-terra) an AIMessage's `.content`
    is a LIST of blocks — reasoning (with encrypted_content), then text — not a plain string.
    `str()` on that list yields a raw repr that then leaks into the reply the participant sees
    (session 55d43900). This pulls out only the TEXT blocks (dropping reasoning/encrypted noise),
    and passes a plain string through unchanged.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # keep text blocks; skip reasoning / tool_use / encrypted blocks
                if block.get("type") in (None, "text", "output_text") and block.get("text"):
                    parts.append(str(block["text"]))
        return " ".join(p for p in parts if p).strip()
    return str(content or "")


DiagnosisPhase = Literal[
    "symptom_capture",
    "primary_suspect",
    "suspect_elicitation",
    "prior_actions",
    "entity_resolution",
    "strategy_selection",
    "guided_diagnosis",
    "physical_check",
    "portal_check",
    "visual_guidance",
    "issue_identification",
    "escalation",
]

UserStrategy = Literal[
    "devices_first",
    "connections_first",
    "follow_the_thread",
    "unknown",
]

SelectedMindmap = Literal[
    "device_level_diagnosis",
    "connection_level_diagnosis",
    "dependency_chain_diagnosis",
    "unknown",
]

PrimarySuspectType = Literal["device", "connection", "logic", "context", "unknown"]

# Coarse intent of a single user turn, decided by the router (triage layer).
# Downstream nodes key off this — e.g. suspect elicitation only runs on a fresh
# "symptom" turn, and "location_request" turns go straight to the photo path.
TurnIntent = Literal[
    "greeting",
    "location_request",
    "info",
    "portal",
    "symptom",
    "followup",
    "ambiguous",
    "unknown",
]

NextActionType = Literal[
    "ask_clarification",
    "tool_check",
    "physical_check",
    "portal_check",
    "visual_guidance",
    "explain",
    "conclude",
    "escalate",
    "none",
]


class EvidenceItem(TypedDict, total=False):
    source: Literal["user", "tool", "system", "scenario", "portal"]
    content: str
    status: Literal["reported", "verified", "assumed", "rejected", "unknown"]
    related_device_id: str
    related_tool: str


class HypothesisItem(TypedDict, total=False):
    id: str
    description: str          # the candidate CAUSE, one short falsifiable phrase
    status: Literal["untested", "testing", "supported", "rejected", "resolved", "unknown"]
    related_device_id: str
    # The single cheapest check that would confirm or reject THIS cause — the field the
    # ranking is about. The board is ordered by how fast/discriminating this check is, so
    # the agent always reaches for the move that settles the most per participant action.
    discriminating_check: str
    rank: int                 # 1 = check this first (cheapest discriminating check)
    evidence_for: list[str]
    evidence_against: list[str]


class VisualOutput(TypedDict, total=False):
    type: Literal[
        "floorplan_position_image",
        "device_photo",
        "portal_screenshot",
        "other",
    ]
    path: str
    configured_path: str
    absolute_path: str
    exists: bool
    related_device_id: str
    caption: str


class ToolRecord(TypedDict, total=False):
    tool_name: str
    tool_args: dict[str, Any]
    tool_result: str
    success: bool


class EvaluatorRecord(TypedDict, total=False):
    """One evaluator verdict, logged whether or not it changed the reply.

    `checks_failed` is the measurement. Counting these across sessions gives a real
    hallucination / duplicate-question / impossible-action rate, instead of an estimate
    reconstructed by reading transcripts afterwards.

    `groundedness_unavailable` guards that measurement's denominator: the LLM judge fails open
    (a dead judge must not block a live participant), so a turn it never saw would otherwise be
    logged as a clean "approved" and silently deflate the hallucination rate.
    """
    turn: int
    verdict: Literal["approved", "revise", "block"]
    checks_failed: list[str]
    feedback: str
    action_type: str
    target_device_id: str
    operated_device_id: str
    reply_text: str
    groundedness_unavailable: bool


class DeductionStep(TypedDict, total=False):
    """One investigative move in the case-file timeline.

    Accumulated deterministically as the diagnosis runs: the agent issues an
    action to test a hypothesis (suspect), then the participant reports back the
    result. Steps stay "open" (empty result) until the participant answers.
    """
    suspect: str          # the hypothesis this move tests
    action: str           # what the agent asked the participant to do
    result: str           # what the participant reported back ("" while open)
    action_type: str      # next_action_type that produced this step
    # Device the check targeted. The evaluator's duplicate check needs it: two
    # near-identical instructions on DIFFERENT devices ("read the Door Sensor
    # State" / "read the Window Sensor State") are distinct checks, not repeats.
    target_device_id: str


class ChecklistItem(TypedDict, total=False):
    """One item of the fixed 12-question diagnostic checklist (Phase 3).

    Seeded with fixed ids in initial_state so nothing from the original
    question list is silently dropped; status advances as the diagnosis runs.
    """
    id: str
    question: str
    status: Literal["unanswered", "in_progress", "answered"]
    answer: str


class PortalContext(TypedDict, total=False):
    portal_available: bool
    portal_mode: Literal["static_guide", "source_code_rag", "live_api", "unknown"]
    last_portal_instruction: str
    last_portal_observation: str
    relevant_screen: str
    requested_values: list[str]
    related_device_id: str


class SmartHomeState(MessagesState, total=False):
    mode: Literal["clarify", "solve", "locate"]

    # coarse intent of the current user turn, set by the router each turn
    turn_intent: TurnIntent

    active_scenario_id: str
    active_scenario_name: str
    user_expertise_level: Literal["novice", "medium", "expert", "unknown"]

    # Which visual condition of the smart-home portal the participant is running.
    # Set once at session start (experimenter's choice) and locked; it only
    # changes how the agent phrases Your Room navigation, since the two portal
    # builds differ solely in that tab (All Devices / All Rules are identical):
    #   "dashboard"  -> Your Room is grouped device cards, no floor map / no graph views
    #   "floor_map"  -> Your Room is a spatial floor map with Devices/Rules/Connections subtabs
    presentation_mode: Literal["dashboard", "floor_map"]

    reported_symptom: str
    normalized_symptom: str

    # primary suspect inferred silently from user's natural language
    primary_suspect_label: str
    primary_suspect_type: PrimarySuspectType

    # how the primary suspect was obtained — important for the study, since it
    # records whether the participant's mind-map came spontaneously or had to be
    # elicited by the agent:
    #   "volunteered" -> named in the symptom description, no question asked
    #   "elicited"    -> participant answered the agent's suspect question
    #   "none"        -> no suspect given even after asking (or policy disabled)
    suspect_source: Literal["volunteered", "elicited", "none", "unknown"]

    # suspect-elicitation step bookkeeping (see suspect_elicitation_policy in the
    # behavior config). Both default to False so the original silent flow is
    # unchanged when the policy is disabled.
    suspect_elicitation_done: bool   # the suspect question has been asked once
    awaiting_suspect_answer: bool    # the next user turn is the suspect answer

    # "what have you tried so far?" follow-up — asked once after the suspect step,
    # before any diagnostic move. prior_actions stores the answer (research data).
    prior_actions: str
    prior_actions_asked: bool
    awaiting_prior_actions: bool

    # final diagnosis surfaced to the case-file panel when the agent concludes
    # or escalates; empty until then.
    root_cause: str
    recommended_action: str

    # fault classification emitted with a conclude/escalate DiagnosticMove (Phase
    # 2). Empty until the agent concludes. Fixes the pilot gap where every case
    # escalated with a generic action and no device/connection/configuration label.
    fault_label: Literal[
        "device_error", "connection_error", "configuration_error", ""
    ]

    # running observation — set after each turn, NOT pre-decided at session start
    strategy_signals: dict[str, int]
    observed_strategy: UserStrategy
    strategy_history: list[dict]

    # back-compat: mirror observed_strategy after each turn (not used as a routing input)
    suspected_strategy: UserStrategy
    selected_mindmap: SelectedMindmap
    diagnosis_phase: DiagnosisPhase

    target_space: str
    target_device: str
    target_device_id: str
    target_device_type: str
    # Device the CURRENT reply asks the participant to physically handle, when that is not
    # the device under test (a bypass check operates the fan to test its plug). Read by the
    # evaluator's affordance check; reset every turn by solve_node, so it is never stale.
    operated_device_id: str
    involved_device_ids: list[str]
    ambiguous_device_candidates: list[str]

    pending_question: str
    last_check_requested: str
    next_action_type: NextActionType
    issue_identified: bool

    # Quick-reply options for the CURRENT agent turn, shown as tappable buttons in
    # the chat UI. Populated only when the requested check has a small closed set
    # of expected answers (e.g. a sensor state: Open / Closed / Unavailable) —
    # open-ended questions and explanations get none. The UI always appends its
    # own "Other…" affordance, so "Other" never appears in this list. Cleared on
    # every turn that doesn't set it, so stale buttons never outlive their question.
    reply_options: list[str]

    # Ordered investigative timeline for the case-file panel:
    # symptom -> (suspect / action / result)* -> root_cause. Built incrementally
    # in solve_node from what the agent asked and what the participant reported.
    deduction_timeline: list[DeductionStep]

    evidence: list[EvidenceItem]
    hypotheses: list[HypothesisItem]
    rejected_hypotheses: list[HypothesisItem]
    confirmed_facts: list[EvidenceItem]

    current_hypothesis_id: str
    current_step_id: str

    checked_devices: list[str]
    checked_tools: list[ToolRecord]

    # Fixed 12-question diagnostic checklist (Phase 3), seeded in initial_state.
    diagnostic_checklist: list[ChecklistItem]

    visual_outputs: list[VisualOutput]
    portal_context: PortalContext

    last_agent_reply: str
    last_tool_summary: str

    # The AGENT's current most-probable cause — one falsifiable phrase, updated per turn from
    # DiagnosticMove.working_hypothesis. Distinct from primary_suspect_*, which records the
    # PARTICIPANT's mental model (a research variable) and must never be overwritten by the
    # agent's inference.
    working_hypothesis: str

    # ── Evaluator (Phase 8) ───────────────────────────────────────────────────
    # evaluate_node audits the DiagnosticMove that solve_node produced, BEFORE the
    # participant ever sees it: is the claim grounded, is the action possible, is it a
    # duplicate, is there enough evidence to conclude.
    #
    # revision_count is capped at 1 retry. A second failure becomes "block", and the graph
    # emits a safe fallback reply rather than looping — a participant sitting in the lab
    # cannot be left waiting while the agent argues with itself.
    evaluator_verdict: str          # "approved" | "revise" | "block"
    evaluator_feedback: str         # fed back into solve_node on "revise"
    revision_count: int

    # Every verdict, INCLUDING approved ones, with the checks that failed. This is the
    # hallucination/duplicate/impossible-action count for the thesis — a measured number
    # rather than an estimated one. Logged whether or not it changed the reply.
    evaluator_log: list[EvaluatorRecord]

    # unique ID for this conversation — generated once in initial_state
    session_id: str

    # set True by the router when the user's first message is just a greeting
    is_greeting: bool