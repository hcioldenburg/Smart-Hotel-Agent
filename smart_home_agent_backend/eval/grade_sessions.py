"""Score finished sessions against the ground truth.

This is the thing that turns configs/ground_truth/ into numbers. Without it you have a
carefully specified answer key and no way to check anything against it.

For every session in logs/chats/, and its scenario's ground-truth file, it answers:

    Did they get the ROOT CAUSE?      -> the correct_root_cause_keywords
    Did they get the FAULT CLASS?     -> device / connection / configuration
    Did they land on a NEAR-MISS?     -> the known_false_conclusions, scored SEPARATELY
    How EFFICIENT were they?          -> turns taken vs min_discriminating_checks
    What did the EVALUATOR catch?     -> duplicates, impossible actions, ungrounded claims

The near-miss column is the one that matters most and is the easiest to throw away. Several
scenarios have a wrong answer that is *reasonable* — the "connection error" on the roller
shutter, the "the fan is broken" on the fan plug. A participant who lands there reasoned
correctly from insufficient evidence, which is a different result from reasoning badly, and
collapsing the two into "wrong" destroys the most interesting finding in the study.

    python -m eval.grade_sessions                    # score every session
    python -m eval.grade_sessions --session <id>
    python -m eval.grade_sessions --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHATS = ROOT.parent / "logs" / "chats"
GROUND_TRUTH = ROOT / "configs" / "ground_truth"

# The agent's fault_label vocabulary and ground truth's fault_class vocabulary are not the
# same words. They mean the same three things.
FAULT_ALIASES: dict[str, str] = {
    "device_error": "device",
    "device_fault": "device",
    "connection_error": "connection",
    "connectivity": "connection",
    "configuration_error": "configuration",
    "rule_config": "configuration",
    "rule_conflict": "configuration",
    "sensor_state": "configuration",
    "ui_mapping": "configuration",
    "power": "device",
}


def _norm_fault(value: Any) -> str:
    return FAULT_ALIASES.get(str(value or "").strip().lower(), "")


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


# Stop-words stripped before matching a keyword phrase, so "only the right" is judged on
# {only, right}, not on the filler "the".
_STOP = {
    "the", "is", "it", "its", "a", "an", "to", "of", "on", "in", "and", "or", "as",
    "that", "this", "was", "were", "be", "been", "so", "not", "no", "at", "by", "for",
}


def _phrase_hit(phrase: str, answer: str) -> bool:
    """Is this required idea present in the answer?

    Exact-substring matching (the old test) under-credited correct diagnoses that used
    different words in a different order: ground truth "only the right" vs the agent's
    "command only Bedlight Right", "smart fan ... lost its connection" vs "Smart Fan has a
    connection issue". A correct answer must not fail on word order. So match the way the
    near-miss scorer already does — on the phrase's distinctive content words — while keeping
    the exact-substring path as a fast accept. Pure synonymy the tokens do not share (ground
    truth "clock is wrong" vs answer "time out of sync") still misses: that is a gap in the
    keyword list, not something a matcher should paper over by guessing."""
    phrase = _text(phrase)
    if not phrase:
        return False
    if phrase in answer:  # exact phrase still counts, unchanged
        return True
    words = [w for w in re.findall(r"[a-z]{3,}", phrase) if w not in _STOP]
    if not words:
        # nothing distinctive to match on (e.g. a phrase of only stop-words / short tokens);
        # fall back to the strict substring test rather than accept on nothing.
        return phrase in answer
    present = sum(1 for w in words if w in answer)
    # A single-word idea must be present; a multi-word idea needs at least half its content
    # words. Mirrors the near-miss threshold, so the two scorers agree on what "present" means.
    return present >= max(1, (len(words) + 1) // 2)


def load_ground_truth() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sid = (data.get("metadata") or {}).get("scenario_id")
        if sid:
            out[sid] = data
    return out


def grade(session: dict, gt: dict) -> dict[str, Any]:
    case = session.get("case_file") or {}
    grading = gt.get("grading") or {}

    # What the agent finally said. Look at the root cause, the recommendation, and the last
    # assistant turn — a conclusion can land in any of them.
    messages = session.get("messages") or []
    final_assistant = ""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            final_assistant = str(m.get("content", ""))
            break
    answer = _text(" ".join([
        str(case.get("root_cause") or ""),
        str(case.get("recommended_action") or ""),
        final_assistant,
    ]))

    # ── root cause: which of the required ideas are present?
    keywords = [str(k).lower() for k in (grading.get("correct_root_cause_keywords") or [])]
    hit = [k for k in keywords if k and _phrase_hit(k, answer)]
    root_cause_correct = bool(keywords) and len(hit) >= max(1, (len(keywords) + 1) // 2)

    # ── fault class
    expected = _norm_fault((gt.get("expected_conclusion") or {}).get("fault_class")
                           or (gt.get("injected_fault") or {}).get("fault_class"))
    given = _norm_fault(case.get("fault_label"))
    fault_class_correct = bool(expected) and given == expected

    # ── near-miss: an ANTICIPATED wrong answer, scored on its own. Not the same as a blunder.
    near_miss = ""
    for wrong in (grading.get("known_false_conclusions") or []):
        # match on the distinctive content words of the anticipated wrong answer
        words = [w for w in re.findall(r"[a-z]{4,}", _text(wrong))][:6]
        if words and sum(w in answer for w in words) >= max(2, len(words) // 2):
            near_miss = " ".join(str(wrong).split())[:90]
            break

    # ── efficiency
    turns = sum(1 for m in messages if m.get("role") == "assistant")
    checks = len([
        s for s in (case.get("deduction_timeline") or [])
        if isinstance(s, dict) and s.get("result")
    ])
    minimum = grading.get("min_discriminating_checks")

    # ── what the evaluator caught, including on approved turns
    log = session.get("evaluator_log") or []
    caught: dict[str, int] = {}
    for rec in log:
        for c in rec.get("checks_failed") or []:
            caught[c] = caught.get(c, 0) + 1
    blocked = sum(1 for r in log if r.get("verdict") == "block")

    return {
        "session_id": session.get("session_id"),
        "scenario_id": session.get("scenario_id"),
        "condition": session.get("presentation_mode"),
        "resolved": bool(session.get("issue_identified")),
        "root_cause_correct": root_cause_correct,
        "keywords_hit": f"{len(hit)}/{len(keywords)}",
        "fault_class_expected": expected,
        "fault_class_given": given or "(none)",
        "fault_class_correct": fault_class_correct,
        "near_miss": near_miss,
        "assistant_turns": turns,
        "checks_completed": checks,
        "min_checks": minimum,
        "over_minimum": (checks - minimum) if isinstance(minimum, int) else None,
        "evaluator_caught": caught,
        "evaluator_blocked": blocked,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Score sessions against configs/ground_truth/.")
    ap.add_argument("--session")
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()

    truth = load_ground_truth()
    if not truth:
        print("No ground truth in configs/ground_truth/. Nothing to grade against.")
        return 1

    if not CHATS.exists():
        print(f"No sessions at {CHATS}. Run some first.")
        return 1

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for path in sorted(CHATS.glob("*.json")):
        session = json.loads(path.read_text(encoding="utf-8"))
        if args.session and session.get("session_id") != args.session:
            continue
        sid = session.get("scenario_id")
        if sid not in truth:
            skipped.append(f"{path.stem} (scenario_id={sid!r})")
            continue
        rows.append(grade(session, truth[sid]))

    print(f"\n══ Session grading ═══════════════════════════════════════════")
    print(f"   {len(rows)} scored against {len(truth)} scenarios\n")

    if skipped:
        print("   skipped — no ground truth for their scenario_id:")
        for s in skipped:
            print(f"     {s}")
        print("   (a session with no scenario_id cannot be graded: nothing says what the answer was)\n")

    if not rows:
        return 0

    hdr = f"{'session':22} {'scenario':28} {'cond':10} {'cause':6} {'class':6} {'turns':5} near-miss"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{str(r['session_id'])[:22]:22} {str(r['scenario_id'])[:28]:28} "
            f"{str(r['condition'])[:10]:10} "
            f"{'YES' if r['root_cause_correct'] else 'no':6} "
            f"{'YES' if r['fault_class_correct'] else 'no':6} "
            f"{r['assistant_turns']:<5} {r['near_miss'][:40]}"
        )

    n = len(rows)
    correct = sum(r["root_cause_correct"] for r in rows)
    cls = sum(r["fault_class_correct"] for r in rows)
    misses = sum(bool(r["near_miss"]) for r in rows)
    caught: dict[str, int] = {}
    for r in rows:
        for k, v in r["evaluator_caught"].items():
            caught[k] = caught.get(k, 0) + v

    print(f"\n   root cause correct : {correct}/{n}")
    print(f"   fault class correct: {cls}/{n}")
    print(f"   anticipated near-misses: {misses}/{n}   <- score these SEPARATELY, not as errors")
    print(f"   evaluator caught   : {caught or 'nothing'}")
    print(f"   evaluator blocked  : {sum(r['evaluator_blocked'] for r in rows)} turn(s)\n")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            for r in rows:
                w.writerow({**r, "evaluator_caught": json.dumps(r["evaluator_caught"])})
        print(f"   written: {args.csv}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
