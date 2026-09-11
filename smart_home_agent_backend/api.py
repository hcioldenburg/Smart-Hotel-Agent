from __future__ import annotations

"""
FastAPI wrapper for the smart-home troubleshooting agent.

This API also serves static image files (mounted at /assets and /outputs) so the
frontend can render floorplans and other visual outputs inline in the chat.

It enriches visual_outputs with a base64 data_url so the frontend can render an
image directly from the chat response; it can also fall back to the /assets or
/outputs URL for the same file.

Run locally (from the parent folder that contains smart_home_agent_backend/):
    python -m smart_home_agent_backend.api
    # or: uvicorn smart_home_agent_backend.api:app --host 0.0.0.0 --port 8000 --reload
"""

import base64
import datetime as dt
import json
import mimetypes
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from smart_home_agent_backend.agent import (
    append_session_strategy_row,
    build_graph,
    initial_state,
    write_session_chat_file,
)
from smart_home_agent_backend.utils.state import content_to_text

load_dotenv()

PACKAGE_ROOT = Path(__file__).resolve().parent  # smart_home_agent/
PROJECT_ROOT = PACKAGE_ROOT.parent

ASSETS_DIR = PACKAGE_ROOT / "assets"
OUTPUTS_DIR = PACKAGE_ROOT / "outputs"

ASSETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Smart Home Diagnostic API")

app.add_middleware(
    CORSMiddleware,
    # Local development: allow the Vite frontend on any localhost port
    # (the smart-hotel UI uses 5172/5173; the agent UI uses 5174), plus any
    # private-LAN address, so a study device (e.g. an iPad on the same Wi-Fi)
    # can reach the app. Study devices normally go through the Vite proxy and
    # are same-origin, but this keeps a direct hit from failing CORS.
    allow_origin_regex=(
        r"http://("
        r"localhost"
        r"|127\.0\.0\.1"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Browser-accessible static routes:
# /assets/floorplans/...
# /outputs/...
app.mount(
    "/assets",
    StaticFiles(directory=str(ASSETS_DIR)),
    name="assets",
)

app.mount(
    "/outputs",
    StaticFiles(directory=str(OUTPUTS_DIR)),
    name="outputs",
)

graph = build_graph()

DB_PATH = PROJECT_ROOT / "logs" / "sessions.db"


def _make_serializable(obj: Any) -> Any:
    """Recursively convert LangChain message objects and other non-JSON types to plain dicts."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # LangChain message objects (AIMessage, HumanMessage, ToolMessage, …)
    role = getattr(obj, "type", None) or getattr(obj, "role", None)
    content = getattr(obj, "content", None)
    if role is not None:
        entry: dict[str, Any] = {
            "role": {"human": "user", "ai": "assistant"}.get(str(role), str(role)),
            # Responses-API content is a block list; store readable text, not its repr.
            "content": content_to_text(content),
        }
        tool_calls = getattr(obj, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = _make_serializable(tool_calls)
        tool_call_id = getattr(obj, "tool_call_id", None)
        if tool_call_id:
            entry["tool_call_id"] = str(tool_call_id)
        name = getattr(obj, "name", None)
        if name:
            entry["name"] = str(name)
        return entry
    # fallback — convert to string rather than crash
    try:
        return str(obj)
    except Exception:
        return None


class SessionStore:
    """SQLite-backed session store. Survives uvicorn restarts."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    state_json  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        now = dt.datetime.utcnow().isoformat()
        blob = json.dumps(_make_serializable(state), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
            """, (session_id, blob, now, now))

    def delete(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def list_all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, state_json, created_at, updated_at "
                "FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            try:
                state = json.loads(row["state_json"])
            except Exception:
                state = {}
            result.append({
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "reported_symptom": state.get("reported_symptom"),
                "diagnosis_phase": state.get("diagnosis_phase"),
                "issue_identified": state.get("issue_identified", False),
                "observed_strategy": state.get("observed_strategy"),
                "scenario_id": state.get("active_scenario_id"),
            })
        return result


session_store = SessionStore(DB_PATH)


class SessionListItem(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    reported_symptom: str | None = None
    diagnosis_phase: str | None = None
    issue_identified: bool = False
    observed_strategy: str | None = None
    scenario_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    active_scenario_id: str | None = None
    # Experimenter's condition choice ("dashboard" | "floor_map"), sent on the
    # first turn of a session. Ignored on later turns (locked for the session).
    presentation_mode: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str

    presentation_mode: str | None = None

    diagnosis_phase: str | None = None
    next_action_type: str | None = None
    turn_intent: str | None = None

    reported_symptom: str | None = None
    normalized_symptom: str | None = None
    target_device: str | None = None
    target_device_id: str | None = None
    target_device_type: str | None = None

    primary_suspect_label: str | None = None
    primary_suspect_type: str | None = None
    suspect_source: str | None = None

    last_check_requested: str | None = None
    pending_question: str | None = None
    issue_identified: bool = False
    root_cause: str | None = None
    recommended_action: str | None = None

    # Quick-reply buttons for the current turn: expected answers to the check the
    # agent just requested (e.g. "Open" / "Closed" / "Unavailable"). Empty when the
    # question is open-ended. The frontend appends its own "Other…" free-text chip.
    reply_options: list[str] = Field(default_factory=list)

    deduction_timeline: list[dict[str, Any]] = Field(default_factory=list)

    visual_outputs: list[dict[str, Any]] = Field(default_factory=list)
    portal_context: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    checked_tools: list[dict[str, Any]] = Field(default_factory=list)
    checked_devices: list[str] = Field(default_factory=list)


class SessionStateResponse(BaseModel):
    session_id: str
    messages: list[dict[str, Any]]

    presentation_mode: str | None = None

    diagnosis_phase: str | None = None
    next_action_type: str | None = None
    turn_intent: str | None = None

    reported_symptom: str | None = None
    normalized_symptom: str | None = None
    target_device: str | None = None
    target_device_id: str | None = None
    target_device_type: str | None = None

    primary_suspect_label: str | None = None
    primary_suspect_type: str | None = None
    suspect_source: str | None = None

    last_check_requested: str | None = None
    pending_question: str | None = None
    issue_identified: bool = False
    root_cause: str | None = None
    recommended_action: str | None = None

    reply_options: list[str] = Field(default_factory=list)

    deduction_timeline: list[dict[str, Any]] = Field(default_factory=list)

    visual_outputs: list[dict[str, Any]] = Field(default_factory=list)
    portal_context: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    checked_tools: list[dict[str, Any]] = Field(default_factory=list)
    checked_devices: list[str] = Field(default_factory=list)


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "unknown"))

    return str(getattr(message, "type", None) or getattr(message, "role", "unknown"))


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return content_to_text(message.get("content", ""))

    return content_to_text(getattr(message, "content", ""))


def _serializable_messages(messages: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for message in messages:
        item: dict[str, Any] = {
            "role": _message_role(message),
            "content": _message_content(message),
        }

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = tool_calls

        if isinstance(message, dict) and message.get("tool_calls"):
            item["tool_calls"] = message.get("tool_calls")

        result.append(item)

    return result


def _extract_reply_from_state(state: dict[str, Any]) -> str:
    if state.get("last_agent_reply"):
        return str(state["last_agent_reply"]).strip()

    for message in reversed(state.get("messages", [])):
        role = _message_role(message)
        content = _message_content(message).strip()

        if role in {"ai", "assistant"} and content:
            return content

    return "I could not generate a response."


def _visual_path_to_local_file(
    path_value: str | None,
    absolute_path: str | None = None,
    configured_path: str | None = None,
) -> Path | None:
    """
    Resolve a visual path to a local file.

    Supports:
    - absolute_path from backend
    - /assets/...
    - /outputs/...
    - smart_home_agent/assets/...
    - smart_home_agent/outputs/...
    - relative paths from project root
    """

    candidates: list[Path] = []

    if absolute_path:
        candidates.append(Path(str(absolute_path).replace("\\", "/")))

    if configured_path:
        configured = str(configured_path).replace("\\", "/")
        candidates.append(Path(configured))
        candidates.append(PROJECT_ROOT / configured)

    if path_value:
        normalized = str(path_value).replace("\\", "/")

        if normalized.startswith("/assets/"):
            relative = normalized.removeprefix("/assets/")
            candidates.append(ASSETS_DIR / relative)

        elif normalized.startswith("/outputs/"):
            relative = normalized.removeprefix("/outputs/")
            candidates.append(OUTPUTS_DIR / relative)

        else:
            candidates.append(Path(normalized))
            candidates.append(PROJECT_ROOT / normalized)
            candidates.append(PACKAGE_ROOT / normalized)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            continue

    return None


def _file_to_data_url(file_path: Path) -> str | None:
    """
    Convert a local image file to a base64 data URL.

    This lets the frontend render images inline directly from the chat
    response, without a separate /assets or /outputs image request.
    """
    try:
        if not file_path.exists() or not file_path.is_file():
            return None

        mime_type, _ = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "image/png"

        encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    except Exception:
        return None


def _enrich_visual_outputs(
    visual_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Add data_url to each visual output if the image exists locally.

    The frontend should use:
    1. visual.data_url (inline base64), or
    2. fallback to visual.path, served from /assets or /outputs.
    """
    enriched: list[dict[str, Any]] = []

    for visual in visual_outputs or []:
        item = dict(visual)

        # Keep existing data_url if tools.py already produced it.
        if item.get("data_url"):
            item["exists"] = True
            enriched.append(item)
            continue

        local_file = _visual_path_to_local_file(
            path_value=item.get("path"),
            absolute_path=item.get("absolute_path"),
            configured_path=item.get("configured_path"),
        )

        if local_file:
            item["exists"] = True
            item["data_url"] = _file_to_data_url(local_file)
        else:
            item["data_url"] = None
            item["exists"] = bool(item.get("exists", False))

        enriched.append(item)

    return enriched


def _state_to_chat_response(session_id: str, state: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        reply=_extract_reply_from_state(state),
        presentation_mode=state.get("presentation_mode"),
        diagnosis_phase=state.get("diagnosis_phase"),
        next_action_type=state.get("next_action_type"),
        turn_intent=state.get("turn_intent"),
        reported_symptom=state.get("reported_symptom"),
        normalized_symptom=state.get("normalized_symptom") or None,
        target_device=state.get("target_device"),
        target_device_id=state.get("target_device_id"),
        target_device_type=state.get("target_device_type"),
        primary_suspect_label=state.get("primary_suspect_label") or None,
        primary_suspect_type=state.get("primary_suspect_type"),
        suspect_source=state.get("suspect_source"),
        last_check_requested=state.get("last_check_requested"),
        pending_question=state.get("pending_question"),
        issue_identified=state.get("issue_identified", False),
        root_cause=state.get("root_cause") or None,
        recommended_action=state.get("recommended_action") or None,
        reply_options=state.get("reply_options", []) or [],
        deduction_timeline=state.get("deduction_timeline", []) or [],
        visual_outputs=_enrich_visual_outputs(state.get("visual_outputs", []) or []),
        portal_context=state.get("portal_context", {}) or {},
        evidence=state.get("evidence", []) or [],
        checked_tools=state.get("checked_tools", []) or [],
        checked_devices=state.get("checked_devices", []) or [],
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "assets_mounted": ASSETS_DIR.exists(),
        "outputs_mounted": OUTPUTS_DIR.exists(),
        "assets_dir": str(ASSETS_DIR),
        "outputs_dir": str(OUTPUTS_DIR),
    }


@app.get("/sessions", response_model=list[SessionListItem])
def list_sessions():
    """Return all sessions ordered by most recently updated."""
    return session_store.list_all()


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid4())

    state = session_store.get(session_id)
    if state is None:
        state = initial_state(
            active_scenario_id=req.active_scenario_id,
            presentation_mode=req.presentation_mode or "dashboard",
        )
        state["session_id"] = session_id

    if req.active_scenario_id and not state.get("active_scenario_id"):
        state["active_scenario_id"] = req.active_scenario_id

    # Lock the condition on the first turn that supplies it; ignore later flips.
    if req.presentation_mode and not state.get("presentation_mode"):
        state["presentation_mode"] = req.presentation_mode

    new_input_state = {
        **state,
        "messages": [
            *state.get("messages", []),
            {"role": "user", "content": req.message},
        ],
    }

    new_state = graph.invoke(
        new_input_state,
        config={"recursion_limit": 10},
    )

    session_store.save(session_id, new_state)

    # File-based logs alongside the SQLite store: rewrite this session's chat
    # JSON every turn (captures abandoned sessions too), and append one strategy
    # row the turn the case first closes. Logging must never break a chat turn.
    try:
        write_session_chat_file(new_state)
        just_closed = (not state.get("issue_identified")) and new_state.get(
            "issue_identified"
        )
        if just_closed:
            append_session_strategy_row(new_state)
    except Exception:
        pass

    return _state_to_chat_response(session_id, new_state)


@app.get("/session/{session_id}", response_model=SessionStateResponse)
def get_session(session_id: str):
    state = session_store.get(session_id)

    if not state:
        raise HTTPException(status_code=404, detail="Session not found.")

    return SessionStateResponse(
        session_id=session_id,
        messages=_serializable_messages(state.get("messages", [])),
        presentation_mode=state.get("presentation_mode"),
        diagnosis_phase=state.get("diagnosis_phase"),
        next_action_type=state.get("next_action_type"),
        turn_intent=state.get("turn_intent"),
        reported_symptom=state.get("reported_symptom"),
        normalized_symptom=state.get("normalized_symptom") or None,
        target_device=state.get("target_device"),
        target_device_id=state.get("target_device_id"),
        target_device_type=state.get("target_device_type"),
        primary_suspect_label=state.get("primary_suspect_label") or None,
        primary_suspect_type=state.get("primary_suspect_type"),
        suspect_source=state.get("suspect_source"),
        last_check_requested=state.get("last_check_requested"),
        pending_question=state.get("pending_question"),
        issue_identified=state.get("issue_identified", False),
        root_cause=state.get("root_cause") or None,
        recommended_action=state.get("recommended_action") or None,
        reply_options=state.get("reply_options", []) or [],
        deduction_timeline=state.get("deduction_timeline", []) or [],
        visual_outputs=_enrich_visual_outputs(state.get("visual_outputs", []) or []),
        portal_context=state.get("portal_context", {}) or {},
        evidence=state.get("evidence", []) or [],
        checked_tools=state.get("checked_tools", []) or [],
        checked_devices=state.get("checked_devices", []) or [],
    )


@app.post("/reset/{session_id}")
def reset_session(session_id: str):
    session_store.delete(session_id)
    return {"status": "reset", "session_id": session_id}


if __name__ == "__main__":
    import uvicorn

    # Backend runs on port 8000 (the smart-hotel UI uses 5173).
    uvicorn.run(
        "smart_home_agent_backend.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )