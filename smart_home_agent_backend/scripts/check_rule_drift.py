"""
Check that what we TELL people about the study rules still matches what Home
Assistant actually does.

There are three descriptions of the same 9 rules, and they drift apart silently:

  1. HA ground truth      configs/HA_bootstrap_snapshots/ha_study_automations.yaml
                          (regenerate with scripts/bootstrap_from_HA_labeled_automations.py)
  2. What the AGENT knows configs/portal/portal_rules.yaml
  3. What the PARTICIPANT Smart_Home_UI_Rework/src/data/devices.ts  (RULE_LINKS)
     is shown

The participant only ever interacts with the dashboard, so (3) is what they can
actually see — but the agent must diagnose against (1). When (2) or (3) drifts from
(1), the agent reasons from a rule that does not exist, or the participant reads an
action the rule never performs, and the fault becomes undiscoverable.

This script does not try to auto-fix anything: the wording in (2) and (3) is a
deliberate human translation of (1) into plain language. It only reports where the
SUBSTANCE has diverged, so a human decides.

Usage:
    python scripts/check_rule_drift.py [--ui PATH_TO_devices.ts]
Exit code 1 if drift is found (so it can gate a commit hook / CI step).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HA_SNAPSHOT = ROOT / "configs" / "HA_bootstrap_snapshots" / "ha_study_automations.yaml"
PORTAL_RULES = ROOT / "configs" / "portal" / "portal_rules.yaml"
DEFAULT_UI = Path(
    r"C:\Users\psanei_ganjeh\Documents\GitHub\smart-hotel-lab\Smart_Home_UI_Rework\src\data\devices.ts"
)

# How old the HA snapshot may be before we stop trusting it as "truth".
STALE_AFTER = timedelta(days=7)

# Every entity a rule may touch — as an actuator OR as a trigger/condition — mapped to
# the design ID the UI uses. Sensors must be here too: the UI lists them under
# `sensors`, so leaving them out would make every trigger look like a UI fabrication.
ENTITY_TO_UI_ID = {
    # actuators
    "light.doorlight": "wld",
    "light.windowlight": "wlw",
    "light.bedlight_l": "bll",
    "light.bedlight_r": "blr",
    "light.floorlamp": "flr",
    "cover.rollo": "rsh",
    "cover.0x54ef441000c939e0": "cur",
    "fan.smartfan": "fan",
    "climate.heater": "htr",
    # the TV is reachable under several entities (status sensor, media player, remote)
    "binary_sensor.tv_status": "tv",
    "media_player.fire_tv_192_168_178_22": "tv",
    "remote.fire_tv_192_168_178_22": "tv",
    # sensors (triggers / conditions)
    "binary_sensor.presencesensor_presence": "pres",
    "binary_sensor.sensor_door_contact": "door",
    "binary_sensor.sensor_window_contact": "win",
    "sensor.temp_humid_temperature": "th",
    # The fan's smart plug. It is a CONDITION input to exp_window_closed_fan_on ("only if the
    # plug is on"), not just a power link — so the participant-facing graph must show it feeding
    # the rule. Missing from this map, the checker reported it as a UI fabrication.
    "switch.socket_fan": "fansock",
}


def load_ha() -> dict:
    if not HA_SNAPSHOT.exists():
        sys.exit(f"missing HA snapshot: {HA_SNAPSHOT}\nRegenerate it with bootstrap_from_HA_labeled_automations.py")
    return yaml.safe_load(HA_SNAPSHOT.read_text(encoding="utf-8"))


def parse_ui_rules(ui_path: Path) -> dict[str, dict]:
    """Pull each RULE_LINKS entry out of devices.ts (it is a TS literal, not JSON)."""
    if not ui_path.exists():
        print(f"  ! UI catalog not found at {ui_path} — skipping participant-facing checks")
        return {}

    text = ui_path.read_text(encoding="utf-8")
    block = re.search(r"RULE_LINKS:\s*RuleLink\[\]\s*=\s*\[(.*?)\n\];", text, re.S)
    if not block:
        print("  ! could not locate RULE_LINKS in devices.ts — skipping participant-facing checks")
        return {}

    # Split on each entry's start rather than matching a terminator, so the LAST
    # entry is not dropped (it has no following "{ ruleId" to look ahead to).
    chunks = re.split(r"\{\s*ruleId:", block.group(1))[1:]

    rules: dict[str, dict] = {}
    for chunk in chunks:
        m_id = re.match(r"\s*'([^']+)'", chunk)
        if not m_id:
            continue
        rid, body = m_id.group(1), chunk

        def arr(field: str, body: str = body) -> list[str]:
            m = re.search(rf"{field}:\s*\[(.*?)\]", body, re.S)
            return re.findall(r"'([^']*)'", m.group(1)) if m else []

        rules[rid] = {
            "targets": arr("targets"),
            "sensors": arr("sensors"),
            "actions": arr("actions"),
            "triggers": arr("triggers"),
            "conditions": arr("conditions"),
        }
    return rules


# ── Semantic drift: does the rule's TEXT contradict what HA actually does? ────────────
#
# This checker used to compare only device links and actions, and that is how the worst bug in
# the project survived: the UI told the participant that Temp Low Heater On fires when the
# "Temperature drops BELOW the threshold", while HA fires it when the temperature is ABOVE 19.
# Every device link matched. Every action matched. The checker was green — and the scenario was
# UNSOLVABLE, because the configuration fault the participant is asked to find was displayed to
# them as correct.
#
# A rule's DESCRIPTION is evidence the participant reads. When it says the opposite of what the
# rule does, no amount of good reasoning can recover.

_DIRECTION = {
    "above": "above", "over": "above", "greater": "above", "rises": "above", "exceeds": "above",
    "below": "below", "under": "below", "drops": "below", "falls": "below", "less": "below",
}


def _directions(text: str) -> set[str]:
    """Which way a threshold comparison points, in plain words."""
    words = re.findall(r"[a-z]+", text.lower())
    return {_DIRECTION[w] for w in words if w in _DIRECTION}


def check_semantics(rid: str, ha_rule: dict, portal_rule: dict, ui_rule: dict) -> list[str]:
    """Flag a described rule that contradicts the real one."""
    problems: list[str] = []

    ha_text = " ".join(
        (ha_rule.get("trigger_summary") or []) + (ha_rule.get("condition_summary") or [])
    )
    ha_dirs = _directions(ha_text)
    if not ha_dirs:
        return problems  # no threshold in this rule; nothing to contradict

    for label, described in (
        ("portal_rules.yaml (the agent)", " ".join(
            [str(portal_rule.get("trigger", ""))]
            + [str(c) for c in portal_rule.get("conditions") or []]
            + [str(portal_rule.get("description", ""))]
        )),
        ("the participant-facing UI", " ".join(
            (ui_rule.get("triggers") or []) + (ui_rule.get("conditions") or [])
        )),
    ):
        described_dirs = _directions(described)
        if described_dirs and not (described_dirs & ha_dirs):
            problems.append(
                f"{rid}: SEMANTIC DRIFT — HA's threshold points {'/'.join(sorted(ha_dirs))}, "
                f"but {label} describes it as {'/'.join(sorted(described_dirs))}.\n"
                f"      HA   : {ha_text.strip()[:110]}\n"
                f"      shown: {described.strip()[:110]}\n"
                f"      A rule described as doing the OPPOSITE of what it does cannot be "
                f"diagnosed from its description. If the inversion IS the injected fault, the "
                f"description must show the real (wrong) behaviour, not the intended one."
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", type=Path, default=DEFAULT_UI)
    args = ap.parse_args()

    ha = load_ha()
    portal = yaml.safe_load(PORTAL_RULES.read_text(encoding="utf-8"))
    ui = parse_ui_rules(args.ui)

    problems: list[str] = []

    generated = ha.get("metadata", {}).get("generated_at", "")
    try:
        age = datetime.now() - datetime.fromisoformat(generated)
        stale = age > STALE_AFTER
        print(f"HA snapshot: {generated}  ({age.days}d old){'  <-- STALE' if stale else ''}")
        if stale:
            problems.append(
                f"HA snapshot is {age.days} days old. It is the ground truth everything else is checked "
                f"against, so refresh it before trusting this report:\n"
                f"      python scripts/bootstrap_from_HA_labeled_automations.py --ha-url http://localhost:8123 "
                f"--token <TOKEN> --output-dir configs/HA_bootstrap_snapshots"
            )
    except ValueError:
        problems.append(f"HA snapshot has an unreadable generated_at: {generated!r}")

    ha_rules = ha["automations"]
    portal_rules = {k: v for k, v in portal["rules"].items() if ".exp" in k}

    print(f"rules — HA: {len(ha_rules)}  portal_rules: {len(portal_rules)}  UI: {len(ui)}\n")

    # --- membership ---
    for rid in sorted(set(ha_rules) - set(portal_rules)):
        problems.append(f"{rid}: in HA but MISSING from portal_rules.yaml (agent does not know this rule exists)")
    for rid in sorted(set(portal_rules) - set(ha_rules)):
        problems.append(f"{rid}: in portal_rules.yaml but NOT in HA (agent believes in a rule that does not exist)")
    if ui:
        for rid in sorted(set(ha_rules) - set(ui)):
            problems.append(f"{rid}: in HA but MISSING from the UI (participant cannot see this rule)")
        for rid in sorted(set(ui) - set(ha_rules)):
            problems.append(f"{rid}: shown to the participant but NOT in HA")

    # --- substance ---
    for rid in sorted(set(ha_rules) & set(portal_rules)):
        h = ha_rules[rid]
        actions = h.get("action_summary") or []

        # Does the rule's DESCRIPTION contradict the rule? See check_semantics.
        problems += check_semantics(rid, h, portal_rules[rid], ui.get(rid, {}))

        # A rule with no actions does nothing. Both descriptions must say so.
        p_action = str(portal_rules[rid].get("action", ""))
        p_says_none = "none" in p_action.lower() or not p_action.strip()
        if not actions and not p_says_none:
            problems.append(
                f"{rid}: HA has NO actions (the rule does nothing), but portal_rules describes an action: {p_action!r}"
            )
        if actions and p_says_none:
            problems.append(
                f"{rid}: HA HAS actions {actions}, but portal_rules says it has none"
            )

        if rid in ui:
            ui_actions = ui[rid]["actions"]
            if not actions and ui_actions:
                problems.append(
                    f"{rid}: HA has NO actions, but the participant is shown {ui_actions}. "
                    f"The fault is undiscoverable — they read an action the rule never performs."
                )
            if actions and not ui_actions:
                problems.append(f"{rid}: HA has actions but the UI shows none to the participant")

            # Which devices does HA actually touch, vs which does the UI claim?
            # Both directions matter: a device HA touches but the UI omits is invisible
            # to the participant; a device the UI claims but HA never touches is a lie
            # they will chase.
            involved = set(h.get("entities_involved") or [])
            ha_ids = {ENTITY_TO_UI_ID[e] for e in involved if e in ENTITY_TO_UI_ID}
            ui_ids = set(ui[rid]["targets"]) | set(ui[rid]["sensors"])

            for missing in sorted(ha_ids - ui_ids):
                problems.append(
                    f"{rid}: HA touches {missing}, but the UI does not list it "
                    f"(targets={ui[rid]['targets']}, sensors={ui[rid]['sensors']})"
                )
            for extra in sorted(ui_ids - ha_ids):
                problems.append(
                    f"{rid}: the UI tells the participant this rule involves {extra}, "
                    f"but HA does not touch it. HA touches: {sorted(ha_ids)}"
                )

    # --- anything the device registry could not resolve ---
    unresolved = [
        rid for rid, h in ha_rules.items()
        if any("UNRESOLVED" in str(a) for a in (h.get("action_summary") or []))
    ]
    if unresolved:
        problems.append(
            "these rules have device-targeted actions the registry could not resolve, so their real "
            f"targets are unverifiable: {unresolved}. Regenerate the snapshot while HA is reachable."
        )

    if problems:
        print(f"DRIFT FOUND ({len(problems)}):\n")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n")
        return 1

    print("No drift: HA, portal_rules.yaml and the participant-facing UI agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
