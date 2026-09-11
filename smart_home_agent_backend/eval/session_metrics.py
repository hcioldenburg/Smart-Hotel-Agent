"""Offline session-metrics harness (Phase 10).

Computes per-session and aggregate metrics from the persisted research
artifacts, with no dependency on the live graph:

  * logs/chats/<session_id>.json  -- full transcript + case_file + checklist
                                     (written by agent.write_session_chat_file)
  * logs/evaluator.jsonl          -- one record per evaluator verdict
                                     (written by the Phase 8 evaluator; OPTIONAL,
                                     absent until Phase 8 ships)

Transcript-derivable metrics (message/char/tool counts, checklist completion,
case-file outcome incl. fault_label) work on any saved session, including the
pre-existing pilot logs. Evaluator-derived metrics (checks_failed counts,
duplicates caught, expectation triggers, hallucination flags, duration) are only
populated when an evaluator log is present.

Run:  python -m smart_home_agent_backend.eval.session_metrics [--chats DIR] [--evaluator FILE]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Project root: .../smart_home_agent_backend/eval/session_metrics.py -> parent.parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CHATS_DIR = _PROJECT_ROOT / "logs" / "chats"
_DEFAULT_EVALUATOR_LOG = _PROJECT_ROOT / "logs" / "evaluator.jsonl"
_DEFAULT_SUMMARY_OUT = _PROJECT_ROOT / "logs" / "metrics_summary.json"

# The evaluator (Phase 8) must tag each failed check with one of these slugs in
# its `checks_failed` list, so this harness can count them. Keep in sync with
# evaluator.py.
CHECK_SLUGS = (
    "duplicate",            # check 1: duplicate action/device
    "affordance",           # check 2: impossible physical check
    "expectation",          # check 3: expectation-vs-configuration not asked
    "evidence_sufficiency", # check 4: concluded without enough evidence
    "groundedness",         # check 5: reply not grounded in evidence (hallucination)
)

_TERMINAL_STATUS_ANSWERED = "answered"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_chat_records(chats_dir: Path | str = _DEFAULT_CHATS_DIR) -> list[dict[str, Any]]:
    """Load every logs/chats/*.json transcript. Skips unreadable files."""
    chats_dir = Path(chats_dir)
    records: list[dict[str, Any]] = []
    if not chats_dir.exists():
        return records
    for path in sorted(chats_dir.glob("*.json")):
        try:
            data = _load_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            data.setdefault("session_id", path.stem)
            records.append(data)
    return records


def load_evaluator_log(
    path: Path | str = _DEFAULT_EVALUATOR_LOG,
) -> dict[str, list[dict[str, Any]]]:
    """Group evaluator verdict records by session_id. Empty if the log is absent."""
    path = Path(path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return grouped
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = str(record.get("session_id", ""))
            grouped.setdefault(session_id, []).append(record)
    return grouped


# --------------------------------------------------------------------------- #
# Per-session metrics
# --------------------------------------------------------------------------- #
def _message_metrics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    lengths: list[int] = []
    role_counts: Counter[str] = Counter()
    tool_calls: Counter[str] = Counter()

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        role_counts[role] += 1
        lengths.append(len(str(msg.get("content", "") or "")))
        for call in msg.get("tool_calls", []) or []:
            name = call.get("name") if isinstance(call, dict) else None
            tool_calls[str(name or "unknown")] += 1

    return {
        "message_count": len(messages or []),
        "user_message_count": role_counts.get("user", 0),
        "assistant_message_count": role_counts.get("assistant", 0),
        "tool_message_count": role_counts.get("tool", 0),
        "char_count_total": sum(lengths),
        "char_count_mean": round(statistics.mean(lengths), 1) if lengths else 0.0,
        "char_count_max": max(lengths) if lengths else 0,
        "tool_call_count": sum(tool_calls.values()),
        "tool_call_names": dict(tool_calls),
    }


def _checklist_metrics(checklist: list[dict[str, Any]]) -> dict[str, Any]:
    items = [c for c in (checklist or []) if isinstance(c, dict)]
    if not items:
        return {"checklist_total": 0, "checklist_answered": 0, "checklist_completion_rate": None}
    answered = sum(1 for c in items if c.get("status") == _TERMINAL_STATUS_ANSWERED)
    return {
        "checklist_total": len(items),
        "checklist_answered": answered,
        "checklist_completion_rate": round(answered / len(items), 3),
    }


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _evaluator_metrics(records: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not records:
        return None

    verdicts: Counter[str] = Counter()
    checks_failed: Counter[str] = Counter()
    unjudged = 0
    for rec in records:
        verdicts[str(rec.get("verdict", "unknown"))] += 1
        for slug in rec.get("checks_failed", []) or []:
            checks_failed[str(slug)] += 1
        if rec.get("groundedness_unavailable"):
            unjudged += 1

    timestamps = sorted(t for t in (_parse_ts(r.get("timestamp")) for r in records) if t)
    duration = (
        (timestamps[-1] - timestamps[0]).total_seconds()
        if len(timestamps) >= 2 else None
    )

    return {
        "verdict_counts": dict(verdicts),
        "checks_failed_counts": {slug: checks_failed.get(slug, 0) for slug in CHECK_SLUGS},
        "checks_failed_total": sum(checks_failed.values()),
        # Named call-outs the spec asks for explicitly:
        "duplicates_caught": checks_failed.get("duplicate", 0),
        "expectation_triggers": checks_failed.get("expectation", 0),
        "hallucination_flags": checks_failed.get("groundedness", 0),
        "impossible_actions_caught": checks_failed.get("affordance", 0),
        "evaluator_turns": len(records),
        # Turns the groundedness judge never saw (it fails open). hallucination_flags is only
        # trustworthy over the JUDGED turns, so report the unjudged count alongside it: a session
        # with 0 flags and 12 unjudged turns is not a clean session, it is an unmeasured one.
        "groundedness_unjudged_turns": unjudged,
        "groundedness_judged_turns": len(records) - unjudged,
        "duration_seconds": duration,
    }


def compute_session_metrics(
    chat_record: dict[str, Any],
    evaluator_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """All metrics for one session from its transcript (+ optional evaluator log)."""
    case_file = chat_record.get("case_file", {}) or {}
    metrics: dict[str, Any] = {
        "session_id": chat_record.get("session_id"),
        "scenario_id": chat_record.get("scenario_id"),
        "presentation_mode": chat_record.get("presentation_mode"),
        "observed_strategy": chat_record.get("observed_strategy"),
        "issue_identified": bool(chat_record.get("issue_identified", False)),
        "root_cause": case_file.get("root_cause"),
        "fault_label": case_file.get("fault_label", ""),
        "recommended_action": case_file.get("recommended_action"),
    }
    metrics.update(_message_metrics(chat_record.get("messages", [])))
    metrics.update(_checklist_metrics(chat_record.get("diagnostic_checklist", [])))
    metrics["evaluator"] = _evaluator_metrics(evaluator_records)
    return metrics


# --------------------------------------------------------------------------- #
# Batch + aggregate
# --------------------------------------------------------------------------- #
def compute_all(
    chats_dir: Path | str = _DEFAULT_CHATS_DIR,
    evaluator_log_path: Path | str = _DEFAULT_EVALUATOR_LOG,
) -> list[dict[str, Any]]:
    evaluator_by_session = load_evaluator_log(evaluator_log_path)
    return [
        compute_session_metrics(
            record, evaluator_by_session.get(str(record.get("session_id", "")))
        )
        for record in load_chat_records(chats_dir)
    ]


def _mean(values: Iterable[float]) -> float | None:
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values), 2) if values else None


def aggregate(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics_list:
        return {"session_count": 0}

    fault_labels: Counter[str] = Counter()
    tool_calls: Counter[str] = Counter()
    checks_failed: Counter[str] = Counter()
    for m in metrics_list:
        fault_labels[m.get("fault_label") or "(none)"] += 1
        for name, count in (m.get("tool_call_names") or {}).items():
            tool_calls[name] += count
        ev = m.get("evaluator")
        if ev:
            for slug, count in (ev.get("checks_failed_counts") or {}).items():
                checks_failed[slug] += count

    return {
        "session_count": len(metrics_list),
        "resolved_count": sum(1 for m in metrics_list if m.get("issue_identified")),
        "mean_message_count": _mean(m.get("message_count") for m in metrics_list),
        "mean_char_count_mean": _mean(m.get("char_count_mean") for m in metrics_list),
        "mean_tool_call_count": _mean(m.get("tool_call_count") for m in metrics_list),
        "mean_checklist_completion_rate": _mean(
            m.get("checklist_completion_rate") for m in metrics_list
        ),
        "fault_label_distribution": dict(fault_labels),
        "tool_call_totals": dict(tool_calls),
        "checks_failed_totals": dict(checks_failed) or None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Offline session-metrics harness (Phase 10).")
    parser.add_argument("--chats", default=str(_DEFAULT_CHATS_DIR),
                        help="Directory of logs/chats/*.json transcripts.")
    parser.add_argument("--evaluator", default=str(_DEFAULT_EVALUATOR_LOG),
                        help="Optional logs/evaluator.jsonl verdict log.")
    parser.add_argument("--out", default=str(_DEFAULT_SUMMARY_OUT),
                        help="Where to write the JSON summary.")
    args = parser.parse_args(argv)

    per_session = compute_all(args.chats, args.evaluator)
    summary = {"aggregate": aggregate(per_session), "sessions": per_session}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    agg = summary["aggregate"]
    print(f"Sessions analysed: {agg.get('session_count', 0)}")
    if agg.get("session_count"):
        print(f"  resolved: {agg.get('resolved_count')}")
        print(f"  mean messages/session: {agg.get('mean_message_count')}")
        print(f"  mean tool calls/session: {agg.get('mean_tool_call_count')}")
        print(f"  mean checklist completion: {agg.get('mean_checklist_completion_rate')}")
        print(f"  fault-label distribution: {agg.get('fault_label_distribution')}")
        print(f"  checks_failed totals: {agg.get('checks_failed_totals')}")
    print(f"Summary written to {out_path}")


if __name__ == "__main__":
    main()
