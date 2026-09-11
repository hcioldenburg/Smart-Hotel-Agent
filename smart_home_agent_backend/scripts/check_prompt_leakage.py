"""Prove the agent cannot see the answers.

The study measures how well the agent DIAGNOSES. That number is worth nothing if the
answer is reachable from inside the agent — directly, or as a hint shaped like the answer.
This script is the check.

It assembles every surface the agent can actually see:

  * the real runtime system prompt, for every scenario x every presentation condition
  * the real output of every registered tool, invoked for every device and every task

and tests each surface against the ground-truth files in configs/ground_truth/.

Three tiers, because a leak is rarely a copy-paste:

  L1  VERBATIM      a long phrase from the ground truth appears in a surface.
  L2  ANSWER STATED enough of a scenario's root-cause keywords co-occur in one blob that
                    the blob effectively gives the answer away.
  L3  FAULT-SHAPED  a config blob names an affected device AND the thing that is broken
                    AND a failure verb. It never quotes the answer, but it is only
                    written that way because someone knew the answer. This is the tier
                    that catches worked examples built on a real scenario, and it is the
                    one that actually bites.

L1 and L2 fail the build. L3 warns: it needs a human to judge whether the text is
domain-general (fine) or scenario-specific (a leak).

    python scripts/check_prompt_leakage.py           # all scenarios
    python scripts/check_prompt_leakage.py --strict  # L3 also fails the build

Exit 0 clean, 1 on a leak.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from smart_home_agent_backend.utils import tools as agent_tools  # noqa: E402
from smart_home_agent_backend.utils.config_loader import (  # noqa: E402
    build_compiled_config,
    build_runtime_system_prompt,
)

GROUND_TRUTH_DIR = ROOT / "configs" / "ground_truth"
ACKNOWLEDGED_FILE = GROUND_TRUTH_DIR / "_ACKNOWLEDGED.yaml"
SCENARIO_DIR = ROOT / "configs" / "reasoning-model_scenarios"
PRESENTATION_MODES = ["dashboard", "floor_map"]

# Words that carry no scenario-specific meaning. A match on these is noise: every config
# in the project says "device", "check", "the".
STOPWORDS = {
    "a", "an", "the", "is", "it", "its", "and", "or", "but", "if", "then", "than", "that",
    "this", "these", "those", "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "as", "so", "not", "no", "be", "been", "was", "were", "are", "will", "would", "can",
    "could", "should", "do", "does", "did", "has", "have", "had", "when", "while",
    "which", "who", "what", "how", "why", "where", "there", "their", "they", "them",
    "you", "your", "user", "participant", "agent", "check", "checks", "checking",
    "device", "devices", "room", "dashboard", "smart", "home", "state", "rule", "rules",
    "one", "any", "all", "some", "only", "also", "still", "just", "up", "out",
}

# Verbs that mark a sentence as being ABOUT something being broken. Used only for L3.
FAILURE_VERBS = {
    "broken", "fails", "failing", "failed", "fault", "faulty", "not working",
    "does not work", "doesn't work", "unresponsive", "not responding", "no response",
    "turns off", "turned off", "switches off", "blocks", "blocking", "blocked",
    "prevents", "stale", "offline", "unavailable", "disconnected", "lost", "wrong",
    "misconfigured", "conflict", "overrides", "overriding", "not turn on", "won't",
}

PLACEHOLDER = "ASK_USER"


# ── Ground truth ──────────────────────────────────────────────────────────────────

def _strings(node: Any) -> list[str]:
    """Every string anywhere under a YAML node."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def load_ground_truth() -> list[dict[str, Any]]:
    if not GROUND_TRUTH_DIR.exists():
        return []

    scenarios = []
    for path in sorted(GROUND_TRUTH_DIR.glob("*.yaml")):
        if path.name.startswith("_"):  # _TEMPLATE.yaml
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["_path"] = path
        scenarios.append(data)
    return scenarios


def secret_text(gt: dict[str, Any], verbatim_tier: bool = False) -> list[str]:
    """The parts of a ground-truth file that must never be reachable.

    `symptom.participant_sees` is deliberately NOT secret — the participant is told it, and
    it is what the scenario file feeds the prompt. Everything else is an answer.

    One carve-out, for the verbatim tier only: `evidence_path[].check` is a PROCEDURE
    ("read the clock pill in the top bar"), and procedures have to be phrased in the
    portal's own vocabulary — which portal_tasks.yaml also uses, because that is where the
    agent is legitimately taught to navigate. Matching those against each other reports a
    leak every time and means nothing.

    What is actually secret is not HOW to run a check, but that this is the RIGHT check,
    in this order, and what its result implies. So `discriminates`, `observable` and
    `rules_out` stay in — only the procedural wording comes out, and only for L1. L2 and
    L3 still see the whole evidence path.
    """
    secret: list[str] = []
    for key in ("injected_fault", "root_cause", "expected_conclusion", "grading"):
        secret += _strings(gt.get(key))

    for step in gt.get("evidence_path") or []:
        if not isinstance(step, dict):
            secret += _strings(step)
            continue
        for field, value in step.items():
            if verbatim_tier and field == "check":
                continue
            secret += _strings(value)

    return [s for s in secret if s and s.strip() and PLACEHOLDER not in s]


def load_acknowledged() -> list[str]:
    """Passages a human reviewed and judged domain-general. See _ACKNOWLEDGED.yaml."""
    if not ACKNOWLEDGED_FILE.exists():
        return []
    data = yaml.safe_load(ACKNOWLEDGED_FILE.read_text(encoding="utf-8")) or {}
    return [
        str(e["match"]).lower()
        for e in data.get("acknowledged") or []
        if isinstance(e, dict) and e.get("match")
    ]


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z_]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def phrases(text: str, n: int = 5) -> set[str]:
    """Content-word n-grams — a leak that survives light paraphrase and reformatting."""
    words = content_words(text)
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


PORTAL_RULES_FILE = ROOT / "configs" / "portal" / "portal_rules.yaml"


def _public_rule_name_blobs() -> list[str]:
    """Rule display names are PUBLIC BY CONSTRUCTION — the participant reads them in the
    All Rules list, and build_known_automations_block hands the agent the same names
    (names ONLY, 2026-07-17) so it can send the participant straight to a rule. A
    ground-truth phrase that is merely a slice of this public inventory is therefore
    not a secret.

    Names are concatenated per group in sorted order — exactly how the prompt block
    renders them — so an n-gram spanning two ADJACENT names in the rendered list
    ("...fan off entrance door light...") is sanctioned too, while any phrase touching
    rule CONTENTS (triggers/conditions/actions) still fires.
    """
    try:
        data = yaml.safe_load(PORTAL_RULES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        return []

    groups: dict[str, list[str]] = {}
    for rule in (data.get("rules") or {}).values():
        if not isinstance(rule, dict):
            continue
        name = str(rule.get("display_name") or rule.get("friendly_name") or "").strip()
        if name:
            groups.setdefault(str(rule.get("group", "")), []).append(name)

    return [
        " ".join(content_words(" ".join(sorted(names))))
        for names in groups.values()
        if names
    ]


# ── Surfaces the agent can see ────────────────────────────────────────────────────

def build_surfaces(scenario_id: str | None) -> dict[str, str]:
    """Every piece of text the agent could read, keyed by where it came from."""
    surfaces: dict[str, str] = {}

    compiled = build_compiled_config(scenario_id=scenario_id, save_snapshot=False)

    # A ground-truth section must never have been compiled in at all.
    for key in compiled:
        if "ground_truth" in key or "groundtruth" in key:
            raise SystemExit(
                f"FATAL: compiled config contains a '{key}' section. Ground truth must "
                "never be loaded. Check config_loader.DEFAULT_PATHS."
            )

    for mode in PRESENTATION_MODES:
        prompt = build_runtime_system_prompt(compiled, state={"presentation_mode": mode})
        surfaces[f"system_prompt[{mode}]"] = prompt

    # The tools return slices of config at runtime, so they are part of the surface even
    # though they are not in the prompt. Invoke them for real, over every device/task.
    device_ids = [d["id"] for d in compiled["environment"].get("devices", [])]
    task_names = list((compiled.get("portal_tasks") or {}).get("tasks", {}))

    def _run(tool, label: str, arg: dict[str, Any] | None = None) -> None:
        try:
            surfaces[label] = str(tool.invoke(arg or {}))
        except Exception as exc:  # a tool that cannot run cannot leak
            surfaces[label] = f"<tool error: {exc}>"

    _run(agent_tools.get_space_info, "tool:get_space_info")
    _run(agent_tools.list_devices, "tool:list_devices")
    _run(agent_tools.check_hub_status, "tool:check_hub_status")
    _run(agent_tools.get_portal_overview, "tool:get_portal_overview")
    _run(agent_tools.list_portal_tasks, "tool:list_portal_tasks")

    for did in device_ids:
        _run(agent_tools.get_device_info, f"tool:get_device_info[{did}]", {"device_name_or_id": did})
        _run(agent_tools.get_device_knowledge, f"tool:get_device_knowledge[{did}]", {"device_name_or_id": did})
        _run(agent_tools.get_portal_entity_info, f"tool:get_portal_entity_info[{did}]", {"device_name_or_id": did})
        _run(
            agent_tools.get_device_portal_check_guide,
            f"tool:get_device_portal_check_guide[{did}]",
            {"device_name_or_id": did},
        )
        _run(
            agent_tools.check_device_connectivity,
            f"tool:check_device_connectivity[{did}]",
            {"device_name_or_id": did},
        )

    for task in task_names:
        _run(agent_tools.get_portal_task_guide, f"tool:get_portal_task_guide[{task}]", {"task_name": task})

    return surfaces


def blobs(surface: str, is_structured: bool = False) -> list[str]:
    """Split a surface into units small enough that co-occurrence means something.

    Tool output is JSON and the configs are YAML. Treating a whole payload as one blob
    makes every device co-occur with every failure word in the file, which is how a
    co-occurrence test degrades into noise. Walk structured input to its leaf strings
    instead, so a "blob" is one thing a human actually wrote — a single sentence, the same
    granularity as a paragraph of the prompt.
    """
    text = surface.strip()

    def _leaves(node: Any) -> list[str]:
        if isinstance(node, str):
            return [node]
        if isinstance(node, dict):
            return [s for v in node.values() for s in _leaves(v)]
        if isinstance(node, list):
            return [s for v in node for s in _leaves(v)]
        return []

    if is_structured or text.startswith(("{", "[")):
        for parse in (json.loads, yaml.safe_load):
            try:
                leaves = _leaves(parse(text))
            except Exception:
                continue
            if leaves:
                # A folded YAML scalar is one "sentence" to the author but often several
                # to a reader; split it so a hit points at the actual clause.
                out = []
                for leaf in leaves:
                    out += [
                        p.strip()
                        for p in re.split(r"(?<=[.!?])\s+|\n\s*\n", leaf)
                        if p.strip()
                    ]
                return out

    parts = re.split(r"\n\s*\n|\n- |\n  - |(?<=[.!?])\s{2,}", text.replace("\\n", "\n"))
    return [p.strip() for p in parts if p.strip()]


# ── The three tiers ───────────────────────────────────────────────────────────────

def check_verbatim(gt, surfaces) -> list[str]:
    """L1 — a long phrase from the answer appears in something the agent reads."""
    hits = []
    secrets = secret_text(gt, verbatim_tier=True)
    gt_phrases: set[str] = set()
    for s in secrets:
        gt_phrases |= phrases(s, n=5)

    # Drop phrases that are just slices of the public rule-name inventory (see
    # _public_rule_name_blobs) — matching a name the participant can read on the
    # All Rules screen is not leaking an answer.
    public_blobs = _public_rule_name_blobs()
    gt_phrases = {p for p in gt_phrases if not any(p in blob for blob in public_blobs)}

    if not gt_phrases:
        return hits

    for label, surface in surfaces.items():
        surface_words = " ".join(content_words(surface))
        for phrase in sorted(gt_phrases):
            if phrase in surface_words:
                hits.append(f"[L1 VERBATIM] {label} contains ground-truth phrase: \"{phrase}\"")
    return hits


def check_answer_stated(gt, surfaces) -> list[str]:
    """L2 — enough root-cause keywords land in one blob to give the answer away."""
    keywords = [
        k.lower().strip()
        for k in (gt.get("grading") or {}).get("correct_root_cause_keywords") or []
        if isinstance(k, str) and PLACEHOLDER not in k
    ]
    if len(keywords) < 2:
        return []

    # The participant briefing is PUBLIC BY CONSTRUCTION — it is handed to the participant, and
    # it is the only scenario text the agent is meant to have. Keywords co-occurring inside it
    # are not a leak, they are the task.
    #
    # This fired for real: the briefing names the symptom ("the curtains are not open") and lists
    # the three-way choice every participant is given ("device, connection, or configuration"),
    # so the keywords {curtain, connection} both appear — and the probe called it a leak. It is
    # not. Exclude the briefing's own text from this tier.
    # Match against the text that ACTUALLY reaches the prompt — the scenario file's
    # scenario_description — not against participant_sees. They say the same thing but the
    # scenario file is written in the third person, so a substring test against
    # participant_sees misses it entirely.
    public: set[str] = set()
    sid = (gt.get("metadata") or {}).get("scenario_id", "")
    scenario_file = SCENARIO_DIR / f"{sid}.yaml"
    if scenario_file.exists():
        data = yaml.safe_load(scenario_file.read_text(encoding="utf-8")) or {}
        desc = (data.get("context") or {}).get("scenario_description") or ""
        if desc:
            public.add(" ".join(str(desc).lower().split()))
    briefing = " ".join(
        str((gt.get("symptom") or {}).get("participant_sees") or "").lower().split()
    )
    if briefing:
        public.add(briefing)

    def _is_briefing(blob: str) -> bool:
        norm = " ".join(blob.lower().split())
        if len(norm) < 20:
            return False
        for text in public:
            # The blob is a slice of the public text, or the public text is a slice of it
            # (prompt assembly reflows and prefixes it with "description:").
            if norm in text or text[:150] in norm:
                return True
        return False

    threshold = 2  # two keywords together is already the shape of the answer
    hits = []
    for label, surface in surfaces.items():
        for blob in blobs(surface):
            if _is_briefing(blob):
                continue
            low = blob.lower()
            present = [k for k in keywords if k in low]
            if len(present) >= threshold:
                hits.append(
                    f"[L2 ANSWER STATED] {label} — a single passage contains root-cause "
                    f"keywords {present}: \"{blob[:160].strip()}...\""
                )

    hits += _check_verdict_stated(gt, surfaces, _is_briefing)
    return hits


# The three labels a participant must choose between. If an agent-visible passage pins one of
# these onto a device that IS the affected device, it has stated the verdict — regardless of what
# the grading keywords happen to say.
_FAULT_CLASS_PHRASES = {
    "device_error":     ("device error", "device fault", "device issue", "device problem"),
    "connection_error": ("connection error", "connection fault", "connection issue",
                         "connectivity error", "connectivity issue", "connection problem"),
    "rule_config":      ("configuration error", "configuration fault", "configuration issue",
                         "config error", "misconfigured rule", "rule is wrong",
                         "wrong rule", "configuration problem"),
}


def _check_verdict_stated(gt, surfaces, is_briefing) -> list[str]:
    """L2b — a passage names the scenario's FAULT CLASS next to its affected device.

    This is the bluntest possible leak and the probe used to sail straight past it. A knowledge-base
    entry reading "the roller shutter shows unavailable and the switch responds but it doesn't
    react — IT'S A DEVICE ERROR FOR THE ROLLER SHUTTER" hands over the verdict in one sentence, and
    neither L1 (no long shared phrase) nor L2 (only one grading keyword present) fired on it.

    The grading keywords are a proxy for the answer. THE FAULT CLASS IS the answer — it is literally
    what the participant is asked to name. So test for it directly, and do not rely on whoever wrote
    the keywords having chosen well.
    """
    fault_class = (gt.get("expected_conclusion") or {}).get("fault_class") \
        or (gt.get("injected_fault") or {}).get("fault_class")
    phrases = _FAULT_CLASS_PHRASES.get(str(fault_class), ())
    if not phrases:
        return []

    affected = [
        d for d in (gt.get("symptom") or {}).get("affected_devices") or []
        if isinstance(d, str)
    ]
    if not affected:
        return []

    # Match on how a HUMAN names the device ("roller shutter"), not the entity id ("rollo") —
    # which is exactly the gap that let the canary through.
    names: list[str] = []
    for dev in (gt.get("_environment_devices") or []):
        if dev.get("id") in affected:
            names.append(str(dev.get("name", "")).lower())
            names += [str(a).lower() for a in dev.get("aliases", []) or []]
    names = [n for n in {n for n in names if len(n) >= 3}]

    hits = []
    for label, surface in surfaces.items():
        for blob in blobs(surface):
            if is_briefing(blob):
                continue
            low = blob.lower()
            said = [p for p in phrases if p in low]
            named = [n for n in names if re.search(rf"\b{re.escape(n)}\b", low)]
            if said and named:
                hits.append(
                    f"[L2 VERDICT STATED] {label} — a single passage pins the scenario's own fault "
                    f"class {said} onto its affected device {sorted(set(named))[:2]}. That IS the "
                    f"answer the participant is asked to give:\n        \"{blob[:200].strip()}\""
                )
    return hits


def check_fault_shaped(gt, surfaces, compiled) -> list[str]:
    """L3 — text that is only written that way because the author knew the answer.

    The giveaway is almost never the rule's NAME. It is the causal link: a passage that
    puts the broken device and the thing that breaks it in the same breath, with failure
    language around them. "My fan is not working; I think a rule turns it off when the TV
    turns on" names no rule at all, and hands over the entire answer.

    So fire when a blob ties an `affected_device` to EITHER the fault's own identifier
    (`where`) OR another device the fault mechanism involves (`involves_devices`).
    """
    fault = gt.get("injected_fault") or {}
    where = str(fault.get("where") or "")
    if PLACEHOLDER in where:
        where = ""

    affected_ids = [
        d for d in (gt.get("symptom") or {}).get("affected_devices") or []
        if isinstance(d, str)
    ]
    partner_ids = [
        d for d in fault.get("involves_devices") or []
        if isinstance(d, str) and d not in affected_ids
    ]

    # An id like `smartfan` shows up in configs as "Smart Fan", "Fan", "fan.smartfan".
    # Match on the environment's own aliases so the probe sees what a reader would. Short
    # aliases ("fan", "tv") are kept — they are exactly how a leaked worked-example refers
    # to a device — and word-boundary matching plus the co-occurrence requirement below is
    # what keeps them from matching everything.
    names_by_id: dict[str, list[str]] = {}
    for dev in compiled["environment"].get("devices", []):
        labels = [str(dev.get("id", "")), str(dev.get("name", ""))]
        labels += [str(a) for a in dev.get("aliases", []) or []]
        names_by_id[dev["id"]] = sorted(
            {l.lower() for l in labels if len(l) >= 2}, key=len, reverse=True
        )

    # What identifies the broken thing. `automation.exp_movie_mode_tv_on_fan_off` gives
    # away the answer through "movie", "mode", "exp_movie_mode_tv_on_fan_off" — never
    # through "automation", which is a domain prefix every rule in HA shares.
    HA_DOMAINS = {
        "automation", "light", "cover", "switch", "fan", "climate", "sensor",
        "binary_sensor", "media_player", "script", "input_datetime", "scene",
    }
    where_tokens = {
        w for w in content_words(where)
        if len(w) > 3 and w not in HA_DOMAINS
    }

    def _mentions(needle: str, haystack: str) -> bool:
        return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None

    hits = []
    seen: set[str] = set()
    for label, surface in surfaces.items():
        structured = label.startswith("file:") and label.endswith((".yaml", ".yml"))
        for blob in blobs(surface, is_structured=structured):
            low = blob.lower()

            names_hit = [
                did for did in affected_ids
                if any(_mentions(n, low) for n in names_by_id.get(did, [did]))
            ]
            if not names_hit:
                continue

            verb_hit = [v for v in FAILURE_VERBS if v in low]
            if not verb_hit:
                continue

            where_hit = [w for w in where_tokens if _mentions(w, low)]
            partner_hit = [
                did for did in partner_ids
                if any(_mentions(n, low) for n in names_by_id.get(did, [did]))
            ]
            if not (where_hit or partner_hit):
                continue

            # The same config string is served by many tools; report the text once.
            fingerprint = low[:120]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            tie = where_hit or [f"via {p}" for p in partner_hit]
            hits.append(
                f"[L3 FAULT-SHAPED] {label} — ties affected device {names_hit} to the "
                f"injected fault ({tie}) with failure language {verb_hit[:2]}:\n"
                f"        \"{blob[:200].strip()}\""
            )
    return hits


def check_latent(gt, compiled) -> list[str]:
    """LATENT — a leak sitting in a config key that nothing currently reads.

    Several config blocks are loaded but never rendered into the prompt (see
    docs/config_wiring.md). Text in them is harmless *today* and invisible to the surface
    scan above — which is exactly what makes it dangerous. The day someone wires that key
    in (a reasonable thing to want: some of those blocks are genuinely useful), the answer
    ships to the model, and nothing in the diff would look like a leak.

    So scan the raw config FILES too, and report fault-shaped text found in them. This is
    a warning, never a failure: the text is not reaching the model yet.
    """
    config_dir = ROOT / "configs"
    files = [
        p for p in config_dir.rglob("*.yaml")
        if "ground_truth" not in p.parts
        and "HA_bootstrap_snapshots" not in p.parts
        and "compiled" not in p.parts
    ]

    surfaces = {}
    for path in files:
        try:
            surfaces[f"file:{path.relative_to(ROOT)}"] = path.read_text(encoding="utf-8")
        except OSError:
            continue

    return [
        h.replace("[L3 FAULT-SHAPED]", "[LATENT]")
        for h in check_fault_shaped(gt, surfaces, compiled)
    ]


# ── Main ──────────────────────────────────────────────────────────────────────────

def self_test(ground_truth: list[dict[str, Any]], compiled: dict[str, Any]) -> bool:
    """Prove the detectors still fire.

    A leakage probe that reports "clean" because its matcher quietly broke is worse than
    no probe: it manufactures confidence. So feed each detector a surface that is a known
    leak and assert it is caught.
    """
    print("── Self-test (can the probe still detect a leak?)\n")
    ok = True
    tested = 0

    for gt in ground_truth:
        sid = (gt.get("metadata") or {}).get("scenario_id", "?")

        # A scaffold whose answers are still ASK_USER has no answer to leak. Building a
        # canary out of placeholders and then asserting the detector fires on it would be
        # testing nothing, and would report the probe as broken when it is merely idle.
        statement = str((gt.get("root_cause") or {}).get("statement") or "")
        keywords = [
            k for k in (gt.get("grading") or {}).get("correct_root_cause_keywords") or []
            if isinstance(k, str) and PLACEHOLDER not in k
        ]
        if PLACEHOLDER in statement or len(content_words(statement)) < 5 or len(keywords) < 2:
            print(f"   {sid}: not filled in yet — skipped (fill root_cause + grading to test it)")
            continue

        tested += 1
        secrets = secret_text(gt)
        if not secrets:
            print(f"   {sid}: no non-placeholder answer text yet — nothing to detect.")
            continue

        # L1: paste the root cause verbatim into a fake prompt.
        canary_l1 = {"CANARY[prompt]": (gt.get("root_cause") or {}).get("statement", "")}
        hit_l1 = check_verbatim(gt, canary_l1)

        # L2: a passage that names the root-cause keywords together.
        kws = (gt.get("grading") or {}).get("correct_root_cause_keywords") or []
        canary_l2 = {"CANARY[prompt]": "Remember: " + ", ".join(str(k) for k in kws) + "."}
        hit_l2 = check_answer_stated(gt, canary_l2)

        # L3: a worked example built on the real fault, without quoting the answer.
        fault = gt.get("injected_fault") or {}
        devs = (gt.get("symptom") or {}).get("affected_devices") or []
        names = [
            next(
                (d.get("name", "") for d in compiled["environment"]["devices"] if d["id"] == did),
                did,
            )
            for did in devs
        ]
        canary_l3 = {
            "CANARY[prompt]": (
                f"Example: the {' and '.join(names)} is not working. Check "
                f"{fault.get('where', '')} first."
            )
        }
        hit_l3 = check_fault_shaped(gt, canary_l3, compiled)

        for tier, hits in (("L1", hit_l1), ("L2", hit_l2), ("L3", hit_l3)):
            if hits:
                print(f"   {sid} {tier}: detector fires")
            else:
                print(f"   {sid} {tier}: DETECTOR DID NOT FIRE on a known leak — probe is broken")
                ok = False

    if not tested:
        print("\n   WARNING: no scenario is filled in far enough to test the detectors.")
        print("   The probe cannot vouch for itself, so a 'clean' result below means only")
        print("   'nothing to compare against' — not 'the agent cannot see the answers'.")

    print()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the agent cannot see the answers.")
    parser.add_argument("--strict", action="store_true", help="Fail on L3 warnings too.")
    parser.add_argument("--scenario", help="Only check this scenario id.")
    parser.add_argument(
        "--no-self-test",
        action="store_true",
        help="Skip the canary check that proves the detectors still work.",
    )
    args = parser.parse_args()

    ground_truth = load_ground_truth()
    if args.scenario:
        ground_truth = [
            g for g in ground_truth
            if (g.get("metadata") or {}).get("scenario_id") == args.scenario
        ]

    print("\n══ Prompt leakage probe ══════════════════════════════════════\n")

    if not ground_truth:
        print("  No ground-truth scenarios in configs/ground_truth/ yet.")
        print("  The probe has nothing to test against — it cannot tell you the agent is")
        print("  clean, only that you have not told it what 'clean' means.")
        print("\n  Fill in a copy of _TEMPLATE.yaml and re-run.\n")
        return 0

    failures: list[str] = []
    warnings: list[str] = []
    acknowledged = load_acknowledged()
    n_acked = 0

    if not args.no_self_test:
        base = build_compiled_config(save_snapshot=False)
        if not self_test(ground_truth, base):
            print("  ABORT: the probe's own detectors are broken. Fix them before trusting\n"
                  "  any 'clean' result from this script.\n")
            return 1

    for gt in ground_truth:
        meta = gt.get("metadata") or {}
        sid = meta.get("scenario_id", gt["_path"].stem)
        status = meta.get("status", "draft")

        has_scenario_file = (SCENARIO_DIR / f"{sid}.yaml").exists()
        surfaces = build_surfaces(sid if has_scenario_file else None)
        compiled = build_compiled_config(
            scenario_id=sid if has_scenario_file else None, save_snapshot=False
        )

        print(f"── {sid}  ({status}, {len(surfaces)} surfaces)")
        if not has_scenario_file:
            print(f"   note: no configs/reasoning-model_scenarios/{sid}.yaml — "
                  "checked the scenario-free prompt.")

        # _check_verdict_stated needs the environment's human names for the affected devices —
        # "roller shutter", not "rollo". Attach them rather than re-loading the config.
        gt["_environment_devices"] = compiled["environment"].get("devices", [])

        f1 = check_verbatim(gt, surfaces)
        f2 = check_answer_stated(gt, surfaces)
        w3 = check_fault_shaped(gt, surfaces, compiled)
        w4 = check_latent(gt, compiled)

        # Reviewed-safe passages are dropped from the WARNING tiers only. A verbatim
        # answer (L1) or a stated answer (L2) is never acknowledgeable away.
        before = len(w3) + len(w4)
        w3 = [h for h in w3 if not any(a in h.lower() for a in acknowledged)]
        w4 = [h for h in w4 if not any(a in h.lower() for a in acknowledged)]
        n_acked += before - (len(w3) + len(w4))

        failures += f1 + f2
        warnings += w3 + w4

        if not (f1 or f2 or w3 or w4):
            print("   clean\n")
        else:
            for h in f1 + f2 + w3 + w4:
                print(f"   {h}")
            print()

    print("══ Result ════════════════════════════════════════════════════\n")

    if n_acked:
        print(f"  {n_acked} passage(s) suppressed as reviewed-safe "
              "(configs/ground_truth/_ACKNOWLEDGED.yaml)\n")

    if failures:
        print(f"  LEAK — {len(failures)} answer(s) reachable by the agent:\n")
        for f in failures:
            print(f"  * {f}")
        print()

    if warnings:
        n_latent = sum(1 for w in warnings if "[LATENT]" in w)
        n_live = len(warnings) - n_latent
        print(f"  {len(warnings)} fault-shaped passage(s) — a human must judge these")
        print(f"    {n_live} reaching the model now, {n_latent} latent (in a config key "
              "nothing reads yet)\n")
        for w in warnings:
            print(f"  * {w}")
        print(
            "\n  Ask of each: would this text still be written this way if a DIFFERENT\n"
            "  fault had been injected? If no, it is a leak — rewrite it as a\n"
            "  domain-general principle, or move it to configs/ground_truth/.\n"
            "\n  A LATENT hit is not reaching the model today. It is flagged because the\n"
            "  config key it lives in is dead, and wiring that key in — a normal thing to\n"
            "  do — would ship the answer without the diff looking like a leak.\n"
        )

    if failures or (warnings and args.strict):
        return 1

    if not warnings:
        print("  Clean. No ground-truth answer is reachable from the prompt or any tool.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
