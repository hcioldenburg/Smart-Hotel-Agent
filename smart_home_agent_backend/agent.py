from __future__ import annotations

"""
Main LangGraph setup for the smart-home troubleshooting agent.
"""

import datetime as dt
import json
import pathlib
import uuid
from typing import Any

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from smart_home_agent_backend.utils.state import SmartHomeState, content_to_text
from smart_home_agent_backend.utils.router import route_node
from smart_home_agent_backend.utils.nodes import (
    clarify_node,
    evaluate_node,
    locate_node,
    solve_node,
)
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


# Anchor logs to the project root (parent of this package) so the file logs land
# next to the API's SQLite store regardless of the process's working directory.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SESSION_LOG = _PROJECT_ROOT / "logs" / "sessions.jsonl"
_CHATS_DIR = _PROJECT_ROOT / "logs" / "chats"


def _serialize_messages(messages: list[Any]) -> list[dict]:
    """Convert LangChain message objects or plain dicts to serialisable dicts."""
    out = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "type", None) or getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "") or ""

        # map LangChain type names to readable roles
        role = {"human": "user", "ai": "assistant"}.get(str(role), str(role))

        # content_to_text: Responses-API messages carry a block list, not a string.
        entry: dict[str, Any] = {"role": role, "content": content_to_text(content)}

        # include tool call names if present (useful for research)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"name": tc.get("name"), "args": tc.get("args")}
                if isinstance(tc, dict)
                else {"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {})}
                for tc in tool_calls
            ]

        out.append(entry)
    return out


def _session_id_for(state: dict[str, Any]) -> str:
    return state.get("session_id") or dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")


def _build_case_file(state: dict[str, Any]) -> dict[str, Any]:
    """The deduced case-file summary: symptom -> suspect/action/result* -> cause."""
    return {
        "symptom": state.get("normalized_symptom") or state.get("reported_symptom"),
        "primary_suspect_label": state.get("primary_suspect_label"),
        "primary_suspect_type": state.get("primary_suspect_type"),
        "suspect_source": state.get("suspect_source"),
        # The agent's own running hypothesis — what a "prime suspect" display should show. The
        # participant's suspect above is a research variable (their mental model, verbatim);
        # this is the diagnosis in progress.
        "working_hypothesis": state.get("working_hypothesis", ""),
        "deduction_timeline": state.get("deduction_timeline", []),
        "root_cause": state.get("root_cause"),
        "recommended_action": state.get("recommended_action"),
        "fault_label": state.get("fault_label", ""),
    }


def write_session_chat_file(state: dict[str, Any]) -> None:
    """Overwrite logs/chats/<session_id>.json with the full transcript.

    Safe to call every turn — it always rewrites the one file, so an abandoned
    (never-concluded) session is still captured in its latest state. The
    case-file summary is appended AFTER the transcript so a reader gets the
    conversation first, then the deduced timeline as a closing block.
    """
    _CHATS_DIR.mkdir(parents=True, exist_ok=True)

    chat_record = {
        "session_id": _session_id_for(state),
        "timestamp": dt.datetime.utcnow().isoformat(),
        "scenario_id": state.get("active_scenario_id"),
        "presentation_mode": state.get("presentation_mode"),
        "reported_symptom": state.get("reported_symptom"),
        # What the participant had already tried before asking for help, in their own words.
        # A study variable, and since the elicitation questions were switched off it is only
        # ever captured passively from their opening messages — so if it is not written here
        # it is not recoverable from the session at all.
        "prior_actions": state.get("prior_actions", ""),
        "observed_strategy": state.get("observed_strategy"),
        "issue_identified": state.get("issue_identified", False),
        "diagnostic_checklist": state.get("diagnostic_checklist", []),
        "messages": _serialize_messages(state.get("messages", [])),
        "case_file": _build_case_file(state),
        # Every evaluator verdict this session, approvals included. This is the measurement:
        # a real count of duplicates, impossible actions, premature conclusions and ungrounded
        # claims — rather than one reconstructed by reading transcripts afterwards.
        "evaluator_log": state.get("evaluator_log", []),
    }
    chat_path = _CHATS_DIR / f"{_session_id_for(state)}.json"
    with chat_path.open("w", encoding="utf-8") as fh:
        json.dump(chat_record, fh, ensure_ascii=False, indent=2)


def append_session_strategy_row(state: dict[str, Any]) -> None:
    """Append one strategy-metadata row to logs/sessions.jsonl.

    Append-only, so call this once per session (e.g. when the case closes) to
    keep it one row per finished case rather than one per turn.
    """
    _SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": dt.datetime.utcnow().isoformat(),
        "session_id": _session_id_for(state),
        "scenario_id": state.get("active_scenario_id"),
        "presentation_mode": state.get("presentation_mode"),
        "reported_symptom": state.get("reported_symptom"),
        "primary_suspect_label": state.get("primary_suspect_label"),
        "primary_suspect_type": state.get("primary_suspect_type"),
        "suspect_source": state.get("suspect_source"),
        # Sits with the suspect fields: both are the participant's own account, and this
        # per-session row is what the analysis reads rather than the full transcript.
        "prior_actions": state.get("prior_actions", ""),
        "observed_strategy": state.get("observed_strategy"),
        "strategy_signals": state.get("strategy_signals"),
        "strategy_history": state.get("strategy_history"),
        "issue_identified": state.get("issue_identified", False),
        "root_cause": state.get("root_cause"),
        "fault_label": state.get("fault_label", ""),
        "deduction_timeline": state.get("deduction_timeline", []),
    }
    with _SESSION_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_session_strategy(state: dict[str, Any]) -> None:
    """Write both file logs at once (used by the CLI on exit/reset/close)."""
    append_session_strategy_row(state)
    write_session_chat_file(state)


def build_graph():
    builder = StateGraph(SmartHomeState)

    builder.add_node("route", route_node)
    builder.add_node("clarify", clarify_node)
    builder.add_node("locate", locate_node)
    builder.add_node("solve", solve_node)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_node("evaluate", evaluate_node)

    builder.add_edge(START, "route")

    builder.add_conditional_edges(
        "route",
        lambda state: state["mode"],
        {
            "clarify": "clarify",
            "locate": "locate",
            "solve": "solve",
        },
    )

    builder.add_edge("clarify", END)
    builder.add_edge("locate", END)

    # solve -> tools (loop) while the model still wants information; otherwise solve ->
    # evaluate. tools_condition already answers the first half, so reuse it rather than
    # reimplementing its notion of "is there a pending tool call".
    def route_after_solve(state: dict[str, Any]) -> str:
        return "tools" if tools_condition(state) == "tools" else "evaluate"

    builder.add_conditional_edges(
        "solve",
        route_after_solve,
        {"tools": "tools", "evaluate": "evaluate"},
    )
    builder.add_edge("tools", "solve")

    # The evaluator audits the move BEFORE the participant sees it. One retry, then a safe
    # fallback — never a loop. Someone is sitting in the lab waiting for this reply, and an
    # agent arguing with itself is worse for them than an imperfect answer.
    def route_after_evaluate(state: dict[str, Any]) -> str:
        if (
            state.get("evaluator_verdict") == "revise"
            and int(state.get("revision_count", 0) or 0) <= 1
        ):
            return "solve"
        return END      # approved, or blocked (the fallback reply is already in state)

    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"solve": "solve", END: END},
    )

    return builder.compile()


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "unknown"))
    return str(getattr(message, "type", None) or getattr(message, "role", "unknown"))


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return content_to_text(message.get("content", ""))
    return content_to_text(getattr(message, "content", ""))


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    tool_calls = getattr(message, "tool_calls", None)

    if tool_calls:
        return list(tool_calls)

    if isinstance(message, dict):
        return message.get("tool_calls", []) or []

    return []


def _last_assistant_reply(messages: list[Any]) -> str:
    for message in reversed(messages):
        role = _message_role(message)
        content = _message_content(message).strip()

        if role in {"ai", "assistant"} and content:
            return content

    return ""


def _print_run_trace(state: dict[str, Any]) -> None:
    print("\n================ RUN TRACE ================\n")

    for index, message in enumerate(state.get("messages", []), start=1):
        role = _message_role(message)
        content = _message_content(message)
        tool_calls = _message_tool_calls(message)

        print(f"[{index}] role={role}")

        if content:
            print(content)

        if tool_calls:
            print("\n  TOOL CALLS:")
            for tool_call in tool_calls:
                print(
                    f"   - name={tool_call.get('name')} "
                    f"args={tool_call.get('args')} "
                    f"id={tool_call.get('id')}"
                )

        print("\n------------------------------------------\n")

    print(f"Diagnosis phase: {state.get('diagnosis_phase')}")
    print(f"Next action type: {state.get('next_action_type')}")

    if state.get("target_device_id"):
        print(f"Target device: {state.get('target_device')} ({state.get('target_device_id')})")

    if state.get("portal_context"):
        print("\nPortal context:")
        print(state.get("portal_context"))

    if state.get("visual_outputs"):
        print("\nVisual outputs:")
        for visual in state.get("visual_outputs", []):
            print(f"- {visual.get('path')} | exists={visual.get('exists')}")

    print("\n=============== END RUN TRACE ===============\n")


# Fixed diagnostic checklist (Phase 3). Ordered (id, question) pairs mapped
# directly to the original question list, so every item asked for is tracked.
_DIAGNOSTIC_CHECKLIST: list[tuple[str, str]] = [
    ("portal_condition", "What is the condition of the portal (dashboard/floor map) right now?"),
    ("first_report", "What is the user's problem from their first report?"),
    ("symptom_type", "Is the user pointing at a device, or describing a behavior that didn't happen correctly?"),
    ("first_suspect_check", "Has the first suspect been checked? What was the result?"),
    ("ongoing_reason", "What is being checked right now, and why?"),
    ("checks_done", "What has been checked so far?"),
    ("checks_remaining", "What are the possible next checks?"),
    ("portal_location", "Where should the user be on the portal right now (exact tab/view)?"),
    ("shortest_next_check", "What is the shortest path to the next check from here?"),
    ("expectation_clarified", "If evidence shows the system is technically working, has the user's expectation been asked about?"),
    ("fault_label", "device_error / connection_error / configuration_error"),
    ("escalation_readiness", "Is there enough evidence to escalate, and what's the articulation?"),
]


def _seed_diagnostic_checklist() -> list[dict[str, Any]]:
    return [
        {"id": item_id, "question": question, "status": "unanswered", "answer": ""}
        for item_id, question in _DIAGNOSTIC_CHECKLIST
    ]


def initial_state(
    active_scenario_id: str | None = None,
    presentation_mode: str = "dashboard",
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "messages": [],
        "session_id": str(uuid.uuid4()),
        "presentation_mode": presentation_mode,
        "user_expertise_level": "unknown",
        "diagnosis_phase": "symptom_capture",
        "next_action_type": "none",
        "reply_options": [],
        "issue_identified": False,
        "normalized_symptom": "",
        "deduction_timeline": [],
        "suspect_source": "unknown",
        "suspect_elicitation_done": False,
        "awaiting_suspect_answer": False,
        "prior_actions": "",
        "prior_actions_asked": False,
        "awaiting_prior_actions": False,
        "root_cause": "",
        "recommended_action": "",
        "evidence": [],
        "confirmed_facts": [],
        "hypotheses": [],
        "rejected_hypotheses": [],
        "checked_devices": [],
        "checked_tools": [],
        "diagnostic_checklist": _seed_diagnostic_checklist(),
        "visual_outputs": [],
        "portal_context": {
            "portal_available": True,
            "portal_mode": "static_guide",
        },
    }

    if active_scenario_id:
        state["active_scenario_id"] = active_scenario_id

    return state


if __name__ == "__main__":
    load_dotenv()

    graph = build_graph()
    active_scenario_id: str | None = None
    state = initial_state(active_scenario_id=active_scenario_id)

    print("\nSmart-Home Diagnostic Guide")
    print("Type 'exit' to quit.")
    print("Type 'reset' to clear the current diagnosis.\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in {"exit", "quit"}:
            if state.get("reported_symptom"):
                log_session_strategy(state)
            print("Goodbye!")
            break

        if user_input.lower() == "reset":
            if state.get("reported_symptom"):
                log_session_strategy(state)
            state = initial_state(active_scenario_id=active_scenario_id)
            print("State reset.\n")
            continue

        state = graph.invoke(
            {
                **state,
                "messages": [
                    *state.get("messages", []),
                    {"role": "user", "content": user_input},
                ],
            },
            config={"recursion_limit": 10},
        )

        _print_run_trace(state)

        if state.get("issue_identified"):
            log_session_strategy(state)

        reply = _last_assistant_reply(state.get("messages", []))
        print("\nAgent:", reply or "I could not generate a response.", "\n")