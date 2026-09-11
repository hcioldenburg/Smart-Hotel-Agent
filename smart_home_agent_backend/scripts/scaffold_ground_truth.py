"""Draft one ground-truth file per study automation, derived from Home Assistant.

Every factual field is read out of configs/HA_bootstrap_snapshots/ha_study_automations.yaml
— what the rule triggers on, what it acts on, which devices it touches. Nothing is
invented. The fields that CANNOT be derived are written as ASK_USER, because only the
person who built the lab knows them:

  * whether this rule is a scenario at all, or just part of the furniture
  * what was CHANGED to inject the bug (a physical fault leaves no trace in HA)
  * how to put it back

Re-running is safe: an existing file is never overwritten, so anything you have filled in
survives. Delete a draft to have it regenerated.

    python scripts/scaffold_ground_truth.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "configs" / "HA_bootstrap_snapshots" / "ha_study_automations.yaml"
ENVIRONMENT = ROOT / "configs" / "environment" / "Offis_smart_studio.yaml"
OUT_DIR = ROOT / "configs" / "ground_truth"

# HA entity -> environment device id. The agent, the UI and the participant all speak in
# environment ids; HA speaks in entity ids. Ground truth has to be in the former.
ENTITY_TO_DEVICE = {
    "light.doorlight": "doorlight",
    "light.windowlight": "windowlight",
    "light.floorlamp": "floorlamp",
    "light.bedlight_l": "bedlight_l",
    "light.bedlight_r": "bedlight_r",
    "fan.smartfan": "smartfan",
    "switch.socket_fan": "socket_fan",
    "climate.heater": "heater",
    "cover.rollo": "rollo",
    "cover.0x54ef441000c939e0": "curtain",
    "binary_sensor.sensor_door_contact": "doorsensor",
    "binary_sensor.sensor_window_contact": "windowsensor",
    "binary_sensor.presencesensor_presence": "presencesensor",
    "sensor.temp_humid_temperature": "temperaturesensor",
    "binary_sensor.tv_status": "tv",
    "media_player.fire_tv_192_168_178_22": "tv",
    "script.1779809457657": "tv",       # TV_On — an IR publish via the hidden blaster
    "script.new_script": "tv",          # TV_OnOff
    "script.tv_off": "tv",
}


def devices_for(entities: list[str]) -> list[str]:
    out: list[str] = []
    for e in entities:
        did = ENTITY_TO_DEVICE.get(e)
        if did and did not in out:
            out.append(did)
    return out


def action_targets(automation: dict[str, Any]) -> list[str]:
    """Devices the rule ACTS ON — the ones a participant would see misbehave.

    Steps flagged [DISABLED] are skipped: they are switched off in the HA editor and never
    run, so nothing they name can be a symptom. (Reading them as live is what made the
    snapshot claim TV-On-Bedlight-Ambient still drives Bedlight L after it had been fixed.)
    """
    targets: list[str] = []
    for line in automation.get("action_summary") or []:
        if "[DISABLED" in line:
            continue
        for entity, did in ENTITY_TO_DEVICE.items():
            if entity in line and did not in targets:
                targets.append(did)
    return targets


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"No HA snapshot at {SNAPSHOT}. Run bootstrap_from_HA_labeled_automations.py first.")
        return 1

    snapshot = yaml.safe_load(SNAPSHOT.read_text(encoding="utf-8"))
    automations = snapshot.get("automations", {})
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n══ Ground-truth scaffolding ══════════════════════════════════")
    print(f"   from {SNAPSHOT.relative_to(ROOT)} ({len(automations)} study automations)\n")

    written, skipped = 0, 0

    for entity_id, auto in sorted(automations.items()):
        slug = entity_id.replace("automation.exp_", "").replace("automation.", "")
        scenario_id = f"SC-{slug.upper().replace('_', '-')}"
        path = OUT_DIR / f"{scenario_id}.yaml"

        if path.exists():
            print(f"   keep   {path.name}  (already exists — not overwritten)")
            skipped += 1
            continue

        involved = devices_for(auto.get("entities_involved") or [])
        targets = action_targets(auto)
        disabled = [l for l in auto.get("action_summary") or [] if "[DISABLED" in l]

        doc = {
            "metadata": {
                "scenario_id": scenario_id,
                "name": auto.get("friendly_name", slug),
                "status": "draft",
                "validated_on": None,
            },
            "symptom": {
                "participant_sees": "ASK_USER",
                "affected_devices": targets,
                "unaffected_but_suspicious": [d for d in involved if d not in targets],
            },
            "injected_fault": {
                "is_a_study_scenario": "ASK_USER",
                "fault_class": "ASK_USER",
                "mechanism": "ASK_USER",
                "where": entity_id,
                "where_note": "ASK_USER",
                "involves_devices": involved,
                "reversible_by": "ASK_USER",
                # Confirm the room is in the expected start state BEFORE the session. A rig that
                # has silently drifted produces a session that runs perfectly and measures the
                # wrong thing — and the transcript will look like a participant who reasoned
                # badly. You cannot tell those apart afterwards, so the check has to be up front.
                "setup_checklist": [],
                "teardown_checklist": [],
                # Anything the participant must never learn is staged (cf. the TV's IR blaster,
                # the sensors' fake indicator lights). The agent must not learn it either — the
                # leakage probe treats apparatus as an answer.
                "hidden_apparatus": [],
                "observable_in_dashboard": "ASK_USER",
                "observable_note": "ASK_USER",
                # What can only be seen by looking at the real device in the room. Often the
                # decisive check, and by definition not on any screen.
                "observable_in_room": "ASK_USER",
            },
            "root_cause": {
                "statement": "ASK_USER",
                "why_it_produces_symptom": "ASK_USER",
            },
            "evidence_path": [],
            "distractors": [],
            # The experimenter picks the condition per session and the two builds are NOT
            # interchangeable. Write evidence_path as WHAT to observe, not which screen to open
            # (the observations are the same in both); record the navigation differences here.
            #
            # BOTH: All Devices (State, Last changed, a device's Attached Rules) and All Rules.
            # dashboard (:5172): Your Room = grouped cards, controls inline. No floor map, no
            #                    Connections view, no dependency graph.
            # floor_map (:5173): Your Room = floor map + Devices/Rules/Connections sub-rail;
            #                    tapping a device opens a right-hand panel.
            #
            # floor_map only — the Connections view is an informational TOPOLOGY diagram (every
            # device is wired to the hub). It is not a status display and cannot show a broken
            # link. If your answer is a connection fault, a participant may read a solid line as
            # "the connection is fine". Note it here and watch for it in the transcripts.
            "per_condition": {
                "shared": "ASK_USER",
                "dashboard": "ASK_USER",
                "floor_map": "ASK_USER",
            },
            "expected_conclusion": {
                "fault_class": "ASK_USER",
                "technician_handoff": "ASK_USER",
            },
            "grading": {
                "correct_root_cause_keywords": [],
                "min_discriminating_checks": None,
                "known_false_conclusions": [],
            },
        }

        header = [
            "# GROUND TRUTH — never loaded into the agent. See README.md in this directory.",
            "#",
            "# Scaffolded by scripts/scaffold_ground_truth.py from the live HA snapshot.",
            "# Every field below marked ASK_USER is one that CANNOT be read out of Home",
            "# Assistant — most importantly the injected fault itself, because a fault injected",
            "# physically (a pulled module, a re-paired switch) leaves no trace in HA at all.",
            "#",
            "# FIRST QUESTION, before any of the others:",
            "#   injected_fault.is_a_study_scenario — is this rule a scenario you actually",
            "#   inject a bug into, or is it just part of the room's normal behaviour? If it is",
            "#   the latter, delete this file. Not every automation is a scenario.",
            "#",
            "# ── What Home Assistant says this rule does ──────────────────────────────────",
            f"#   enabled now : {auto.get('enabled')}",
            f"#   last fired  : {auto.get('last_triggered')}",
        ]
        for line in auto.get("trigger_summary") or []:
            header.append(f"#   trigger     : {line}")
        for line in auto.get("condition_summary") or []:
            header.append(f"#   condition   : {line}")
        for line in auto.get("action_summary") or []:
            header.append(f"#   action      : {line}")
        if disabled:
            header += [
                "#",
                "#   NOTE: this rule has action steps switched OFF in the HA editor. They stay",
                "#   in the config but never run, so they cannot cause a symptom. They are",
                "#   excluded from affected_devices below.",
            ]
        header.append("# ─────────────────────────────────────────────────────────────────────────────")
        header.append("")

        body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=95)
        path.write_text("\n".join(header) + body, encoding="utf-8")
        print(f"   write  {path.name}  (acts on: {', '.join(targets) or 'nothing'})")
        written += 1

    print(f"\n   {written} drafted, {skipped} left alone.\n")
    print("   Next: open each one. Answer is_a_study_scenario first — delete the files that")
    print("   are not scenarios, then fill in the ASK_USER fields on the ones that are.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
