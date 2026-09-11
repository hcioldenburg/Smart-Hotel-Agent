"""Aggregate a batch of simulated runs into one report of issues.

Reuses eval.grade_sessions.grade() so every run is scored by the SAME rubric a real session
gets (root cause, fault class, near-miss, efficiency, evaluator catches). This file only
aggregates across runs and surfaces the failures — it adds no new scoring of its own, so the
report can never disagree with grade_sessions.

    python -m eval.report --dir logs/sim_runs/20260717T2210     # score one batch
    python -m eval.report                                       # newest batch under logs/sim_runs/
    python -m eval.report --dir logs/chats                      # score the REAL sessions too
    python -m eval.report --dir <batch> --csv out.csv --fail-transcripts

What it flags per scenario, aggregated over its runs:
    resolved / root-cause / fault-class rates   (higher = better)
    near-miss rate                              (the anticipated wrong answer — read separately)
    avg agent turns and avg over-minimum         (efficiency / wandering)
    evaluator catches                            (duplicate_check, ungrounded_claim, fault_type_*)
Then it lists the individual runs that failed, each with a one-line reason and its log path.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from smart_home_agent_backend.eval.grade_sessions import grade, load_ground_truth  # noqa: E402

DEFAULT_SIM_ROOT = ROOT.parent / "logs" / "sim_runs"


def _newest_batch() -> Path | None:
    if not DEFAULT_SIM_ROOT.exists():
        return None
    batches = [p for p in DEFAULT_SIM_ROOT.iterdir() if p.is_dir()]
    return max(batches, key=lambda p: p.stat().st_mtime, default=None)


def _fail_reason(row: dict[str, Any]) -> str:
    """One short line saying why a run is worth a human's eye. Empty = clean."""
    reasons: list[str] = []
    if not row["resolved"]:
        reasons.append("never resolved")
    if not row["root_cause_correct"]:
        reasons.append(f"root cause missed ({row['keywords_hit']})")
    if not row["fault_class_correct"]:
        reasons.append(f"fault class {row['fault_class_given']}≠{row['fault_class_expected']}")
    over = row.get("over_minimum")
    if isinstance(over, int) and over >= 3:
        reasons.append(f"+{over} checks over min (wandered)")
    caught = row.get("evaluator_caught") or {}
    for k in ("duplicate_check", "fault_type_unresolved", "fault_type_mismatch"):
        if caught.get(k):
            reasons.append(f"{k}×{caught[k]}")
    return "; ".join(reasons)


def _rate(values: list[bool]) -> str:
    n = len(values)
    return f"{sum(values)}/{n}" if n else "0/0"


def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregate simulated runs into an issues report.")
    ap.add_argument("--dir", type=Path, default=None,
                    help="folder of run JSONs (default: newest under logs/sim_runs/)")
    ap.add_argument("--csv", type=Path, help="also write per-run rows to CSV")
    ap.add_argument("--fail-transcripts", action="store_true",
                    help="print the message transcript of each failing run")
    args = ap.parse_args()

    run_dir = args.dir or _newest_batch()
    if not run_dir or not run_dir.exists():
        print("No run folder. Give --dir, or run `python -m eval.simulate` first.")
        return 1

    truth = load_ground_truth()
    files = sorted(run_dir.glob("*.json"))
    if not files:
        print(f"No run JSONs in {run_dir}.")
        return 1

    rows: list[dict[str, Any]] = []
    sessions: dict[str, dict] = {}
    skipped: list[str] = []
    for path in files:
        session = json.loads(path.read_text(encoding="utf-8"))
        sid = session.get("scenario_id")
        if sid not in truth:
            skipped.append(path.name)
            continue
        row = grade(session, truth[sid])
        row["_path"] = str(path)
        row["_persona"] = (session.get("sim") or {}).get("persona", "")
        row["_model_calls"] = (session.get("sim") or {}).get("model_calls")
        rows.append(row)
        sessions[str(path)] = session

    print(f"\n══ Simulation report ═══════════════════════════════════════════")
    print(f"   {len(rows)} run(s) from {run_dir}")
    if skipped:
        print(f"   skipped (no ground truth): {len(skipped)}")

    # ── per-scenario aggregation ────────────────────────────────────────────────────
    by_scn: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_scn[str(r["scenario_id"])].append(r)

    hdr = (f"\n{'scenario':30} {'runs':4} {'resolv':7} {'cause':7} {'class':7} "
           f"{'nearMiss':8} {'avgTurns':8} {'avgOver':7} {'avgCalls':8}")
    print(hdr)
    print("-" * len(hdr))
    for sid in sorted(by_scn):
        rs = by_scn[sid]
        turns = [r["assistant_turns"] for r in rs]
        overs = [r["over_minimum"] for r in rs if isinstance(r.get("over_minimum"), int)]
        calls = [r["_model_calls"] for r in rs if isinstance(r.get("_model_calls"), int)]
        print(
            f"{sid[:30]:30} {len(rs):<4} "
            f"{_rate([r['resolved'] for r in rs]):7} "
            f"{_rate([r['root_cause_correct'] for r in rs]):7} "
            f"{_rate([r['fault_class_correct'] for r in rs]):7} "
            f"{_rate([bool(r['near_miss']) for r in rs]):8} "
            f"{statistics.mean(turns):<8.1f} "
            f"{(statistics.mean(overs) if overs else 0):<7.1f} "
            f"{(statistics.mean(calls) if calls else 0):<8.1f}"
        )

    # ── overall ─────────────────────────────────────────────────────────────────────
    n = len(rows)
    caught: dict[str, int] = defaultdict(int)
    for r in rows:
        for k, v in (r.get("evaluator_caught") or {}).items():
            caught[k] += v
    print(f"\n   overall: resolved {_rate([r['resolved'] for r in rows])}   "
          f"root-cause {_rate([r['root_cause_correct'] for r in rows])}   "
          f"fault-class {_rate([r['fault_class_correct'] for r in rows])}   "
          f"near-miss {_rate([bool(r['near_miss']) for r in rows])}")
    all_calls = [r["_model_calls"] for r in rows if isinstance(r.get("_model_calls"), int)]
    if all_calls:
        print(f"   model transactions / run: avg {statistics.mean(all_calls):.1f}  "
              f"(min {min(all_calls)}, max {max(all_calls)}, total {sum(all_calls)})")
    print(f"   evaluator caught across all runs: {dict(caught) or 'nothing'}")

    # ── the failures worth a human's eye ─────────────────────────────────────────────
    failing = [(r, _fail_reason(r)) for r in rows]
    failing = [(r, why) for r, why in failing if why]
    print(f"\n── runs to review: {len(failing)}/{n}")
    for r, why in failing:
        name = Path(r["_path"]).name
        print(f"   [{r['scenario_id']} · {r['condition']} · {r['_persona']}] {why}")
        print(f"       {name}")
        if args.fail_transcripts:
            for m in sessions[r["_path"]].get("messages", []):
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    who = "U" if m["role"] == "user" else "A"
                    print(f"         {who}: {' '.join(m['content'].split())[:140]}")
            print()

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            fields = [k for k in rows[0] if not k.startswith("_")] + ["_persona", "_path"]
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({**r, "evaluator_caught": json.dumps(r["evaluator_caught"])})
        print(f"\n   per-run rows written: {args.csv}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
