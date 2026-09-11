"""
bootstrap_from_ha.py

One-time script to pull live state from Home Assistant and validate
or regenerate the agent's YAML knowledge base.

Run from the lab network (or with SSH tunnel active) BEFORE the study.
Do NOT run during study sessions.

Usage (PowerShell, with SSH tunnel active on localhost:8123):
    python scripts/bootstrap_from_ha.py `
        --ha-url http://localhost:8123 `
        --token YOUR_LONG_LIVED_TOKEN `
        --output-dir configs/bootstrap_snapshot `
        --portal-entities smart_home_agent/configs/portal/portal_entities.yaml

Outputs (written to --output-dir):
    ha_entity_snapshot.yaml       - every live HA entity with state and friendly name
    ha_entity_list_by_domain.yaml - entities grouped by domain (light, sensor, automation...)
    ha_automation_detail.yaml     - automation friendly names and last-triggered states
    portal_entities_diff.yaml     - ✓/✗ check of every entity ID in your existing YAML

The script NEVER overwrites your existing portal_entities.yaml.
Fields like diagnostic_note, portal_locations, and ui_category
must remain hand-annotated — the HA API has no concept of them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is not installed.")
    print("Run: pip install requests")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: 'pyyaml' is not installed.")
    print("Run: pip install pyyaml")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def ha_get(base_url: str, token: str, path: str) -> dict | list:
    """Make a GET request to the HA REST API."""
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"\n  ERROR: Could not connect to {base_url}")
        print("  Make sure your SSH tunnel is active and HA is running.")
        print("  Test with: curl http://localhost:8123/api/")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 401:
            print(f"\n  ERROR: Unauthorized (401) — token is invalid or expired.")
            print("  Generate a new Long-Lived Access Token in HA → Profile → Security.")
        else:
            print(f"\n  ERROR: HTTP {resp.status_code} on {path}: {e}")
        sys.exit(1)


def save_yaml(data: dict, path: Path, label: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tag = f" ({label})" if label else ""
    print(f"  ✓ saved{tag}: {path}")


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all_states(ha_url: str, token: str) -> list[dict]:
    """Pull every entity state from /api/states."""
    print("  Fetching /api/states ...")
    states = ha_get(ha_url, token, "/api/states")
    print(f"  → {len(states)} entities found")
    return states


def verify_connection(ha_url: str, token: str) -> None:
    """Check the API is reachable before doing anything else."""
    print(f"  Testing connection to {ha_url}/api/ ...")
    result = ha_get(ha_url, token, "/api/")
    message = result.get("message", "") if isinstance(result, dict) else ""
    if "API running" in message or isinstance(result, dict):
        print(f"  ✓ Connected — {message or 'API responded'}")
    else:
        print(f"  ✓ Connected")


# ── Parse ─────────────────────────────────────────────────────────────────────

def build_entity_summary(states: list[dict]) -> dict[str, dict]:
    """
    Build a flat summary keyed by entity_id.
    Captures: friendly_name, state, device_class, domain.
    """
    summary = {}
    for s in states:
        eid = s["entity_id"]
        attrs = s.get("attributes", {})
        domain = eid.split(".")[0]
        summary[eid] = {
            "friendly_name": attrs.get("friendly_name", eid),
            "state": s.get("state"),
            "domain": domain,
            "device_class": attrs.get("device_class"),
            "last_changed": s.get("last_changed"),
        }
    return summary


def group_by_domain(states: list[dict]) -> dict[str, list[str]]:
    """Group entity IDs by their domain."""
    grouped: dict[str, list[str]] = {}
    for s in states:
        domain = s["entity_id"].split(".")[0]
        grouped.setdefault(domain, []).append(s["entity_id"])
    # Sort domains and entity lists for readable output
    return {k: sorted(v) for k, v in sorted(grouped.items())}


def extract_automations(states: list[dict]) -> dict[str, dict]:
    """Extract automation entities with their friendly names and states."""
    automations = {}
    for s in states:
        eid = s["entity_id"]
        if not eid.startswith("automation."):
            continue
        attrs = s.get("attributes", {})
        automations[eid] = {
            "friendly_name": attrs.get("friendly_name", eid),
            "state": s.get("state"),  # 'on' = enabled, 'off' = disabled
            "last_triggered": attrs.get("last_triggered"),
        }
    return dict(sorted(automations.items()))


# ── Diff ──────────────────────────────────────────────────────────────────────

def diff_against_portal_entities(
    existing_yaml: dict,
    live_summary: dict[str, dict],
) -> dict:
    """
    For every device declared in portal_entities.yaml,
    check whether its home_assistant_entity_id exists in live HA.
    Returns a structured diff report.
    """
    results = {
        "summary": {
            "checked_at": datetime.now().isoformat(),
            "total_checked": 0,
            "ok": 0,
            "not_found": 0,
            "name_mismatch": 0,
            "no_entity_id": 0,
        },
        "devices": {},
        "portal_only_entities": {},
        "automations": {},
    }

    def check_entity(dev_key: str, dev_info: dict, section: str) -> dict:
        eid = dev_info.get("home_assistant_entity_id")

        if not eid:
            results["summary"]["no_entity_id"] += 1
            return {
                "status": "NO_ENTITY_ID",
                "note": "No home_assistant_entity_id declared in portal_entities.yaml",
            }

        results["summary"]["total_checked"] += 1

        if eid not in live_summary:
            results["summary"]["not_found"] += 1
            return {
                "status": "NOT_FOUND",
                "declared_entity_id": eid,
                "problem": "Entity ID not found in live HA — may be wrong, renamed, or device offline",
            }

        live = live_summary[eid]
        declared_portal_name = dev_info.get("portal_name", "")
        live_friendly = live["friendly_name"]

        # Flag if friendly name differs significantly from portal name
        name_ok = (
            declared_portal_name.lower().replace(" ", "") ==
            live_friendly.lower().replace(" ", "")
        )

        if not name_ok:
            results["summary"]["name_mismatch"] += 1

        results["summary"]["ok"] += 1
        return {
            "status": "OK" if name_ok else "NAME_MISMATCH",
            "declared_entity_id": eid,
            "live_state": live["state"],
            "live_friendly_name": live_friendly,
            "declared_portal_name": declared_portal_name,
            **({"name_note": f"Portal name '{declared_portal_name}' differs from HA friendly name '{live_friendly}'"} if not name_ok else {}),
        }

    # Check devices section
    for dev_key, dev_info in existing_yaml.get("devices", {}).items():
        results["devices"][dev_key] = check_entity(dev_key, dev_info, "devices")

    # Check portal_only_entities section
    for dev_key, dev_info in existing_yaml.get("portal_only_entities", {}).items():
        results["portal_only_entities"][dev_key] = check_entity(dev_key, dev_info, "portal_only_entities")

    # Check declared automations in rules_attached across all devices
    declared_automations: set[str] = set()
    for dev_info in existing_yaml.get("devices", {}).values():
        for auto_eid in dev_info.get("rules_attached", []):
            declared_automations.add(auto_eid)

    live_automations = extract_automations(
        [{"entity_id": eid, "attributes": {"friendly_name": info["friendly_name"]}, "state": info["state"]}
         for eid, info in live_summary.items() if eid.startswith("automation.")]
    )

    for auto_eid in sorted(declared_automations):
        if auto_eid not in live_summary:
            results["automations"][auto_eid] = {
                "status": "NOT_FOUND",
                "problem": "Automation declared in rules_attached but not found in live HA",
            }
        else:
            live = live_summary[auto_eid]
            results["automations"][auto_eid] = {
                "status": "OK",
                "live_state": live["state"],
                "live_friendly_name": live["friendly_name"],
            }

    return results


# ── Print summary ─────────────────────────────────────────────────────────────

def print_diff_summary(diff: dict) -> None:
    summary = diff["summary"]
    print(f"\n  ── Diff results ──────────────────────────────────────")
    print(f"  Entities checked : {summary['total_checked']}")
    print(f"  ✓ OK             : {summary['ok']}")
    print(f"  ✗ Not found      : {summary['not_found']}")
    print(f"  ⚠ Name mismatch  : {summary['name_mismatch']}")
    print(f"  – No entity ID   : {summary['no_entity_id']}")
    print()

    all_items = (
        list(diff["devices"].items()) +
        list(diff["portal_only_entities"].items()) +
        list(diff["automations"].items())
    )

    problems = [(k, v) for k, v in all_items if v.get("status") not in ("OK",)]
    if not problems:
        print("  All declared entity IDs found and matched in live HA.")
    else:
        print("  Items needing attention:")
        for key, result in problems:
            status = result.get("status", "?")
            eid = result.get("declared_entity_id", key)
            note = result.get("problem") or result.get("name_note", "")
            icon = "✗" if status == "NOT_FOUND" else "⚠"
            print(f"    {icon} {key:35s} {eid}")
            if note:
                print(f"      → {note}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap YAML knowledge base from live Home Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # With SSH tunnel (tunnel must be active first)
  python scripts/bootstrap_from_ha.py \\
      --ha-url http://localhost:8123 \\
      --token YOUR_TOKEN \\
      --output-dir configs/bootstrap_snapshot

  # Directly on the lab machine (no tunnel needed)
  python scripts/bootstrap_from_ha.py \\
      --ha-url http://192.168.178.28:8123 \\
      --token YOUR_TOKEN \\
      --output-dir configs/bootstrap_snapshot
        """,
    )
    parser.add_argument(
        "--ha-url",
        required=True,
        help="Home Assistant base URL, e.g. http://localhost:8123",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="HA Long-Lived Access Token (create in HA → Profile → Security)",
    )
    parser.add_argument(
        "--output-dir",
        default="configs/bootstrap_snapshot",
        help="Directory to write output YAML files (default: configs/bootstrap_snapshot)",
    )
    parser.add_argument(
        "--portal-entities",
        default="smart_home_agent/configs/portal/portal_entities.yaml",
        help="Path to existing portal_entities.yaml to diff against",
    )
    args = parser.parse_args()

    out = Path(args.output_dir)
    portal_entities_path = Path(args.portal_entities)

    print(f"\n══ Sherlock Home — HA Bootstrap Script ══════════════════════")
    print(f"  HA URL     : {args.ha_url}")
    print(f"  Output dir : {out}")
    print(f"  Diff target: {portal_entities_path}")
    print()

    # 1. Verify connection
    print("── Step 1: Verify connection ─────────────────────────────────")
    verify_connection(args.ha_url, args.token)

    # 2. Fetch all states
    print("\n── Step 2: Fetch all entity states ───────────────────────────")
    all_states = fetch_all_states(args.ha_url, args.token)

    # 3. Parse
    print("\n── Step 3: Parse entities ────────────────────────────────────")
    entity_summary = build_entity_summary(all_states)
    grouped = group_by_domain(all_states)
    automations = extract_automations(all_states)
    print(f"  Domains found: {', '.join(grouped.keys())}")
    print(f"  Automations  : {len(automations)}")

    # 4. Diff
    print("\n── Step 4: Diff against portal_entities.yaml ─────────────────")
    existing = load_yaml(portal_entities_path)
    if not existing:
        print(f"  WARNING: Could not load {portal_entities_path} — skipping diff")
        diff = {}
    else:
        diff = diff_against_portal_entities(existing, entity_summary)
        print_diff_summary(diff)

    # 5. Save outputs
    print("── Step 5: Save output files ─────────────────────────────────")

    # Full entity snapshot
    save_yaml(
        {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "ha_url": args.ha_url,
                "total_entities": len(entity_summary),
                "note": (
                    "Auto-generated snapshot of live HA state. "
                    "Use to verify entity IDs in portal_entities.yaml. "
                    "Do NOT use as a direct replacement — "
                    "portal_locations, diagnostic_notes, and ui_categories must remain hand-annotated."
                ),
            },
            "entities": entity_summary,
        },
        out / "ha_entity_snapshot.yaml",
        label="full entity snapshot",
    )

    # Grouped by domain
    save_yaml(
        {
            "metadata": {"generated_at": datetime.now().isoformat()},
            "domains": grouped,
        },
        out / "ha_entity_list_by_domain.yaml",
        label="entities by domain",
    )

    # Automation detail
    save_yaml(
        {
            "metadata": {"generated_at": datetime.now().isoformat()},
            "automations": automations,
        },
        out / "ha_automation_detail.yaml",
        label="automation detail",
    )

    # Diff report
    if diff:
        save_yaml(
            diff,
            out / "portal_entities_diff.yaml",
            label="diff report",
        )

    print(f"\n══ Done ══════════════════════════════════════════════════════")
    print(f"  Review portal_entities_diff.yaml for any ✗ or ⚠ entries.")
    print(f"  Fix those in your hand-annotated portal_entities.yaml.")
    print(f"  The agent reads from that file — not from these snapshots.\n")


if __name__ == "__main__":
    main()