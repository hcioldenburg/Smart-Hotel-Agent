"""
extract_from_ha.py

Extracts two things from live Home Assistant:
  1. All devices in the smart hotel with their properties and
     involvement in study automations.
  2. Only automations in STUDY_AUTOMATION_IDS, with full
     trigger / condition / action detail via WebSocket API.

Run with SSH tunnel active (localhost:8123 → lab HA).

Usage (PowerShell):
    python scripts/extract_from_ha.py `
        --ha-url http://localhost:8123 `
        --token YOUR_LONG_LIVED_TOKEN `
        --output-dir configs/bootstrap_snapshot

Dependencies:
    pip install requests pyyaml websocket-client

Outputs:
    ha_devices_with_automations.yaml   — all devices + study automation involvement
    ha_study_automations.yaml          — full config of study automations via WebSocket
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests  # type: ignore[import]
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: 'pyyaml' not installed. Run: pip install pyyaml")
    sys.exit(1)

try:
    import websocket  # type: ignore[import]
except ImportError:
    print("ERROR: 'websocket-client' not installed. Run: pip install websocket-client")
    sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────

STUDY_TAG = "Psanei_Thesis_Experiment"

STUDY_AUTOMATION_IDS = {
    "automation.exp_window_open_shutter_open",
    "automation.exp_door_window_open_fan_off",
    "automation.exp_window_closed_fan_on",
    "automation.exp_entrance_door_light_on",
    "automation.exp_morning_routine",
    "automation.exp_movie_mode_tv_on_fan_off",
    "automation.exp_tv_on_bedlight_ambient",
    "automation.exp_evening_wind_down",
    "automation.exp_temp_low_heater_on"
}

DEVICE_DOMAINS = {
    "light", "cover", "switch", "fan", "climate",
    "binary_sensor", "sensor", "media_player",
    "input_boolean", "input_number", "script",
}

SKIP_DOMAINS = {
    "persistent_notification", "person", "zone", "sun", "weather",
    "update", "button", "number", "select", "text", "device_tracker",
    "remote", "stt", "tts", "wake_word", "assist_pipeline",
    "conversation", "event", "scene",
}


# ── REST helpers ──────────────────────────────────────────────────────────────

def ha_get(base_url: str, token: str, path: str) -> dict | list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(f"{base_url}{path}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"\n  ERROR: Cannot connect to {base_url}")
        print("  Is the SSH tunnel active? Test: curl http://localhost:8123/api/")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 401:
            print("\n  ERROR: 401 Unauthorized — token invalid or expired.")
            print("  Create a new one: HA → Profile → Security → Long-Lived Access Tokens")
        else:
            print(f"\n  ERROR: HTTP {resp.status_code} on {path}: {e}")
        sys.exit(1)


# device_id (HA registry UUID) -> the entity_ids that device exposes.
#
# Automations built in the HA UI target a DEVICE, not an entity, so their actions
# carry an opaque UUID. Without this map an action reads only as
# "device action: cover.set_position" — you cannot tell WHICH cover, which made the
# study rules impossible to verify against the dashboard. Populated in main().
DEVICE_ENTITIES: dict[str, list[str]] = {}


def fetch_device_registry(base_url: str, token: str) -> dict[str, list[str]]:
    """Map every HA device UUID to its entity_ids, via one template call.

    `device_id(entity)` is the inverse of what we want, so we walk all states and
    invert the result — this needs no WebSocket round-trip.
    """
    template = (
        "{% set out = namespace(r=[]) %}"
        "{% for s in states %}"
        "{% set d = device_id(s.entity_id) %}"
        "{% if d %}{% set out.r = out.r + [[d, s.entity_id]] %}{% endif %}"
        "{% endfor %}"
        "{{ out.r | tojson }}"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(f"{base_url}/api/template", headers=headers, json={"template": template}, timeout=30)
    resp.raise_for_status()

    registry: dict[str, list[str]] = {}
    for device_id, entity_id in json.loads(resp.text):
        registry.setdefault(device_id, []).append(entity_id)
    return registry


def resolve_device_action(device_id: str, domain: str) -> str | None:
    """The entity a device-targeted action actually acts on.

    A device exposes many entities (a light also has select.*_power_on_behavior, a
    heater a dozen switches). The one that matters is the entity in the ACTION's own
    domain — cover.set_position on the RollerBlind device means cover.rollo.
    """
    entities = DEVICE_ENTITIES.get(device_id, [])

    for entity_id in entities:
        if entity_id.startswith(f"{domain}."):
            return entity_id

    # Some integrations expose a service whose domain is NOT an entity domain:
    # androidtv.adb_command targets the Fire TV *device*, but the Fire TV's only
    # entity lives in media_player.*, so the domain match above finds nothing and
    # the rule's real target looks unresolvable. Fall back to the device's primary
    # entity — an adb_command aimed at the Fire TV device does act on the Fire TV.
    for entity_id in entities:
        if entity_id.split(".", 1)[0] in DEVICE_DOMAINS:
            return entity_id

    return None


def resolve_config_device_entities(config: dict) -> set[str]:
    """Every entity an automation touches through a DEVICE-targeted step.

    Walks the raw config for {domain, device_id} pairs and resolves each through the
    registry, so a rule built entirely in the HA UI still reports which entities it
    actually acts on.
    """
    found: set[str] = set()

    def _recurse(obj) -> None:
        if isinstance(obj, dict):
            # A switched-off step never runs, so nothing it targets is actually
            # "involved". Do not descend into it.
            if obj.get("enabled") is False:
                return
            # Shape A: {domain, type, device_id}      — a "device action"
            # Shape B: {action|service, target:{device_id}} — a service call aimed at a device
            device_id = obj.get("device_id")
            domain = obj.get("domain")
            if not domain:
                service = obj.get("action") or obj.get("service")
                if service and isinstance(obj.get("target"), dict):
                    domain = str(service).split(".")[0]
                    device_id = obj["target"].get("device_id")

            if device_id and domain:
                ids = device_id if isinstance(device_id, list) else [device_id]
                for did in ids:
                    entity = resolve_device_action(str(did), str(domain))
                    if entity:
                        found.add(entity)
            for value in obj.values():
                _recurse(value)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)

    _recurse(config)
    return found


def save_yaml(data: dict, path: Path, label: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tag = f" ({label})" if label else ""
    print(f"  ✓ saved{tag}: {path}")


# ── WebSocket: fetch all automation configs ───────────────────────────────────

def fetch_automation_configs_via_websocket(ha_url: str, token: str) -> dict[str, dict]:
    """
    Connect to HA WebSocket API and fetch full automation configs.
    Returns a dict keyed by entity_id → full config dict with
    trigger, condition, and action fields.

    HA WebSocket protocol:
      1. Connect to ws://host:port/api/websocket
      2. Receive auth_required message
      3. Send auth message with token
      4. Receive auth_ok
      5. Send command message with type and id
      6. Receive result message with matching id
    """
    # Convert http://localhost:8123 → ws://localhost:8123
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = ws_url.rstrip("/") + "/api/websocket"

    print(f"  Connecting to WebSocket: {ws_url}")

    configs: dict[str, dict] = {}
    msg_id = 1

    try:
        ws = websocket.create_connection(ws_url, timeout=15)
    except Exception as e:
        print(f"\n  ERROR: WebSocket connection failed: {e}")
        print("  Make sure the SSH tunnel is active and HA is running.")
        sys.exit(1)

    def send(payload: dict) -> None:
        ws.send(json.dumps(payload))

    def receive() -> dict:
        raw = ws.recv()
        return json.loads(raw)

    def send_and_receive(payload: dict) -> dict:
        """Send a message and wait for the matching result."""
        target_id = payload["id"]
        send(payload)
        while True:
            msg = receive()
            if msg.get("id") == target_id:
                return msg
            # Ignore unrelated events (state_changed etc)

    try:
        # Step 1: Receive auth_required
        auth_req = receive()
        if auth_req.get("type") != "auth_required":
            print(f"  WARNING: Expected auth_required, got: {auth_req.get('type')}")

        # Step 2: Authenticate
        send({"type": "auth", "access_token": token})
        auth_resp = receive()

        if auth_resp.get("type") == "auth_invalid":
            print("\n  ERROR: WebSocket auth failed — token is invalid.")
            ws.close()
            sys.exit(1)
        elif auth_resp.get("type") != "auth_ok":
            print(f"\n  ERROR: Unexpected auth response: {auth_resp}")
            ws.close()
            sys.exit(1)

        ha_version = auth_resp.get("ha_version", "unknown")
        print(f"  ✓ WebSocket authenticated (HA version: {ha_version})")

        # Step 3: Fetch each study automation config individually
        # In HA 2025.x, config/automation/list is not available via WebSocket.
        # The correct approach is to fetch each automation's config via the
        # /api/config/automation/config/{id} REST endpoint, where {id} is the
        # automation's unique numeric id — retrievable from /api/states attributes.
        # We fetch all automation states first, find the numeric id, then REST-fetch config.
        print(f"  Fetching configs for {len(STUDY_AUTOMATION_IDS)} study automations ...")

        # HA 2025.x supports: "type": "render_template" and entity-specific commands.
        # For automation config, use the websocket command "automation/config" per entity.
        for study_eid in sorted(STUDY_AUTOMATION_IDS):
            # Try websocket automation/config command (works on some HA versions)
            result = send_and_receive({
                "id": msg_id,
                "type": "automation/config",
                "entity_id": study_eid,
            })
            msg_id += 1

            if result.get("success") and result.get("result"):
                configs[study_eid] = result["result"]
                print(f"    ✓ fetched via WS: {study_eid}")
            else:
                # Fallback: try with just the slug as the id field
                slug = study_eid.replace("automation.", "")
                result2 = send_and_receive({
                    "id": msg_id,
                    "type": "automation/config",
                    "automation_id": slug,
                })
                msg_id += 1

                if result2.get("success") and result2.get("result"):
                    configs[study_eid] = result2["result"]
                    print(f"    ✓ fetched via WS (slug): {study_eid}")
                else:
                    print(f"    ✗ WS config not found for: {study_eid} — will try REST")

    finally:
        ws.close()

    # REST fallback for any automations not fetched via WebSocket
    # HA 2025.x exposes automation YAML via /api/config/automation/config/{unique_id}
    # The unique_id is stored in the automation state attributes
    unmatched = STUDY_AUTOMATION_IDS - set(configs.keys())
    if unmatched:
        print(f"\n  REST fallback for {len(unmatched)} automations not fetched via WebSocket ...")
        # We need the numeric/unique id from the state attributes
        for study_eid in sorted(unmatched):
            # Try the slug directly as the config id
            slug = study_eid.replace("automation.", "")
            try:
                cfg = ha_get(ha_url, token, f"/api/config/automation/config/{slug}")
                if isinstance(cfg, dict) and cfg:
                    configs[study_eid] = cfg
                    print(f"    ✓ fetched via REST: {study_eid}")
                    continue
            except Exception:
                pass
            print(f"    ✗ could not fetch config for {study_eid} via REST or WebSocket")

    print(f"\n  → {len(configs)} automation configs fetched total")
    return configs


# ── Parse entity properties ───────────────────────────────────────────────────

def parse_device_entry(state: dict) -> dict:
    eid = state["entity_id"]
    attrs = state.get("attributes", {})
    domain = eid.split(".")[0]

    entry: dict = {
        "entity_id": eid,
        "friendly_name": attrs.get("friendly_name", eid),
        "domain": domain,
        "current_state": state.get("state"),
        "last_changed": state.get("last_changed"),
    }

    if domain == "light":
        entry["brightness"] = attrs.get("brightness")
        entry["color_mode"] = attrs.get("color_mode")
        entry["supported_color_modes"] = attrs.get("supported_color_modes", [])

    elif domain == "cover":
        entry["current_position"] = attrs.get("current_position")
        entry["device_class"] = attrs.get("device_class")

    elif domain in ("binary_sensor", "sensor"):
        entry["device_class"] = attrs.get("device_class")
        entry["unit_of_measurement"] = attrs.get("unit_of_measurement")
        if domain == "sensor":
            entry["state_class"] = attrs.get("state_class")

    elif domain == "climate":
        entry["hvac_mode"] = state.get("state")
        entry["current_temperature"] = attrs.get("current_temperature")
        entry["target_temperature"] = attrs.get("temperature")
        entry["hvac_modes"] = attrs.get("hvac_modes", [])

    elif domain == "fan":
        entry["percentage"] = attrs.get("percentage")
        entry["preset_mode"] = attrs.get("preset_mode")

    elif domain == "switch":
        entry["device_class"] = attrs.get("device_class")

    return entry


# ── Extract entity IDs from config ────────────────────────────────────────────

def extract_entity_ids_from_config(config: dict) -> list[str]:
    """
    Recursively find all readable entity_id references in an automation config.
    Handles HA 2025.x structure where config is wrapped in a 'config' key.
    Skips UUID-style entity references from device-domain actions (not resolvable).
    """
    # Unwrap HA 2025.x nested config
    inner = config.get("config", config)
    found: set[str] = set()

    # HA service call verbs that look like entity IDs but aren't
    SERVICE_SUFFIXES = {
        "turn_on", "turn_off", "toggle", "reload", "trigger",
        "open_cover", "close_cover", "stop_cover", "set_cover_position",
        "set_temperature", "set_hvac_mode", "set_percentage",
        "set_value", "press", "select_option",
    }

    def _is_readable_entity_id(v: str) -> bool:
        """True if value looks like domain.entity_slug, not a UUID or service call."""
        if "." not in v:
            return False
        domain, slug = v.split(".", 1)
        # Exclude known service call suffixes
        if slug in SERVICE_SUFFIXES:
            return False
        # Exclude UUIDs (long hex strings without underscores)
        if len(slug) > 30 and all(c in "0123456789abcdef" for c in slug.replace("-", "")):
            return False
        # Must be a known device domain
        return domain in DEVICE_DOMAINS | {"automation", "input_boolean", "binary_sensor", "input_datetime"}

    def _recurse(obj):
        if isinstance(obj, dict):
            # A switched-off step never runs — see resolve_config_device_entities.
            if obj.get("enabled") is False:
                return
            for k, v in obj.items():
                if k == "entity_id":
                    if isinstance(v, list):
                        found.update(e for e in v if isinstance(e, str) and _is_readable_entity_id(e))
                    elif isinstance(v, str) and _is_readable_entity_id(v):
                        found.add(v)
                else:
                    _recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)
        elif isinstance(obj, str):
            for m in re.findall(r"[a-z_]+\.[a-z0-9_]+", obj):
                domain = m.split(".")[0]
                if domain in DEVICE_DOMAINS | {"automation", "input_boolean"}:
                    found.add(m)

    _recurse(inner)
    return sorted(found)


# ── Human-readable summaries ──────────────────────────────────────────────────

def summarise_triggers(triggers) -> list[str]:
    if not isinstance(triggers, list):
        triggers = [triggers] if triggers else []
    summaries = []
    for t in triggers:
        if not isinstance(t, dict):
            continue
        # HA 2025.x uses 'trigger' key; older versions use 'platform'
        platform = t.get("trigger", t.get("platform", "unknown"))

        # entity_id can be a list in HA 2025.x
        eid_raw = t.get("entity_id", "?")
        eid = ", ".join(eid_raw) if isinstance(eid_raw, list) else str(eid_raw)

        if platform == "device":
            domain = t.get("domain", "?")
            ttype = t.get("type", "?")
            tid = t.get("id", "")
            s = f"device trigger: {domain}.{ttype}"
            if tid:
                s += f" (id={tid})"
            summaries.append(s)
        elif platform == "state":
            frm = t.get("from", "")
            to = t.get("to", "")
            s = f"state change: {eid}"
            if frm:
                s += f" from={frm}"
            if to:
                s += f" to={to}"
            for_val = t.get("for")
            if for_val:
                s += f" for={for_val}"
            summaries.append(s)
        elif platform == "time_pattern":
            summaries.append(
                f"time_pattern: hours={t.get('hours','*')} "
                f"minutes={t.get('minutes','*')} "
                f"seconds={t.get('seconds','*')}"
            )
        elif platform == "time":
            summaries.append(f"time: at={t.get('at','?')}")
        elif platform == "mqtt":
            summaries.append(f"mqtt: topic={t.get('topic','?')}")
        elif platform == "template":
            summaries.append(f"template: {str(t.get('value_template','?'))[:100]}")
        elif platform == "event":
            summaries.append(f"event: {t.get('event_type','?')}")
        elif platform == "numeric_state":
            summaries.append(
                f"numeric_state: {eid} "
                f"above={t.get('above','')} below={t.get('below','')}"
            )
        elif platform == "sun":
            summaries.append(f"sun: event={t.get('event','?')} offset={t.get('offset','0')}")
        else:
            summaries.append(f"{platform}: {str(t)[:100]}")
    return summaries


def summarise_conditions(conditions) -> list[str]:
    if not isinstance(conditions, list):
        conditions = [conditions] if conditions else []
    summaries = []
    for c in conditions:
        if not isinstance(c, dict):
            continue
        ctype = c.get("condition", "unknown")
        if ctype == "state":
            eid_raw = c.get("entity_id", "?")
            eid = ", ".join(eid_raw) if isinstance(eid_raw, list) else eid_raw
            summaries.append(f"state: {eid} == {c.get('state','?')}")
        elif ctype == "time":
            summaries.append(
                f"time: after={c.get('after','?')} before={c.get('before','?')}"
            )
        elif ctype == "numeric_state":
            eid_raw = c.get("entity_id", "?")
            eid = ", ".join(eid_raw) if isinstance(eid_raw, list) else eid_raw
            summaries.append(
                f"numeric_state: {eid} "
                f"above={c.get('above','')} below={c.get('below','')}"
            )
        elif ctype == "template":
            tmpl = str(c.get('value_template','?')).replace('\n', ' ').strip()
            summaries.append(f"template: {tmpl[:150]}")
        elif ctype == "device":
            # HA 2025.x device-domain condition
            domain = c.get("domain", "?")
            cond_type = c.get("type", "?")
            summaries.append(f"device condition: {domain}.{cond_type}")
        elif ctype == "trigger":
            # Branch trigger-id condition inside choose blocks
            tid = c.get("id", "?")
            if isinstance(tid, list):
                tid = ", ".join(tid)
            summaries.append(f"triggered by id={tid}")
        elif ctype in ("and", "or", "not"):
            sub = summarise_conditions(c.get("conditions", []))
            summaries.append(f"{ctype}: [{' | '.join(sub)}]")
        elif "." in str(ctype) and isinstance(c.get("options"), dict):
            # HA's newer entity-domain condition shape, e.g.
            #   condition: temperature.is_value
            #   target:  {entity_id: sensor.x}
            #   options: {threshold: {type: above, value: {number: 19, unit_of_measurement: °C}}}
            #
            # The generic fallback below rendered this as a truncated raw dict — which is
            # useless, and in SC-TEMP-LOW-HEATER-ON it is the single most important line in the
            # rule (the inverted above/below test that IS the injected fault). A condition the
            # summariser cannot read is a condition the drift checker cannot verify.
            target = c.get("target") or {}
            eid_raw = target.get("entity_id", c.get("entity_id", "?"))
            eid = ", ".join(eid_raw) if isinstance(eid_raw, list) else eid_raw

            opts = c["options"]
            threshold = opts.get("threshold") or {}
            direction = threshold.get("type", "?")            # above / below
            value = threshold.get("value")
            if isinstance(value, dict):
                unit = value.get("unit_of_measurement", "")
                value = f"{value.get('number', '?')}{unit}"

            detail = f"{ctype}: {eid} {direction} {value}"
            if opts.get("for") and str(opts["for"]) not in ("00:00:00", "0:00:00"):
                detail += f" for={opts['for']}"
            summaries.append(detail)
        else:
            summaries.append(f"{ctype}: {str(c)[:100]}")
    return summaries


def summarise_actions(actions) -> list[str]:
    if not isinstance(actions, list):
        actions = [actions] if actions else []
    summaries = []
    for a in actions:
        if not isinstance(a, dict):
            continue

        # A step with `enabled: false` is switched off in the HA editor: it stays in
        # the config but never runs. Summarising it as a live action is how the
        # snapshot came to claim TV-On-Bedlight-Ambient still drives Bedlight L, and
        # that Morning Routine still calls media_play — both of which are switched off.
        # Keep it visible (so a disabled step is not silently invisible), but flag it
        # loudly and keep its entities out of `entities_involved`.
        if a.get("enabled") is False:
            inner = summarise_actions([{k: v for k, v in a.items() if k != "enabled"}])
            for line in inner:
                summaries.append(f"[DISABLED - does not run] {line}")
            continue

        # HA 2025.x if/then structure (replaces choose for simple conditionals)
        if "if" in a and "then" in a:
            conds = summarise_conditions(a["if"])
            then_acts = summarise_actions(a["then"])
            else_acts = summarise_actions(a.get("else", []))
            summaries.append(f"if: {' & '.join(conds) or 'condition'}")
            for act in then_acts:
                summaries.append(f"  then: {act}")
            for act in else_acts:
                summaries.append(f"  else: {act}")
            continue

        # HA 2025.x device-domain actions (type/domain/device_id format).
        # The action's entity_id is a registry UUID, so resolve it through the device
        # registry — otherwise the summary cannot say WHICH cover/light it acts on.
        if "type" in a and "domain" in a and "device_id" in a:
            action_type = a.get("type", "?")
            domain = a.get("domain", "?")
            eid_raw = a.get("entity_id", "")
            readable = eid_raw if (eid_raw and "." in str(eid_raw) and len(str(eid_raw)) < 50) else None
            if not readable:
                readable = resolve_device_action(str(a.get("device_id", "")), domain)

            s = f"device action: {domain}.{action_type}"
            if readable:
                s += f" → {readable}"
            else:
                s += " → UNRESOLVED device_id (device registry unavailable)"
            # Surface any data that changes what the action means (e.g. position: 20).
            extras = {k: v for k, v in a.items() if k in ("position", "brightness_pct", "temperature")}
            if extras:
                s += f" | {extras}"
            summaries.append(s)
            continue

        # Standard service/action call
        atype = a.get("action", a.get("service", ""))
        if atype:
            targets = a.get("target", {}) or {}
            target_eids = targets.get("entity_id", [])
            if isinstance(target_eids, str):
                target_eids = [target_eids]

            # A service call can also target a DEVICE rather than an entity
            # (target.device_id). Resolve those through the registry using the
            # service's own domain — e.g. light.turn_on on the "Bedlight R" device
            # means light.bedlight_r.
            target_devs = targets.get("device_id", [])
            if isinstance(target_devs, str):
                target_devs = [target_devs]
            service_domain = str(atype).split(".")[0]
            for did in target_devs:
                entity = resolve_device_action(str(did), service_domain)
                target_eids.append(entity or f"UNRESOLVED:{did}")

            data = a.get("data", {}) or {}
            s = f"{atype}"
            if target_eids:
                s += f" → {', '.join(target_eids)}"
            if data:
                s += f" | {str(data)[:80]}"
            summaries.append(s)

        elif "choose" in a:
            branches = a["choose"]
            summaries.append(f"choose ({len(branches)} branches):")
            for i, branch in enumerate(branches):
                conds = summarise_conditions(branch.get("conditions", []))
                acts = summarise_actions(branch.get("sequence", []))
                summaries.append(f"  branch {i+1} if: {' & '.join(conds) or 'no condition'}")
                for act in acts:
                    summaries.append(f"    then: {act}")
        elif "delay" in a:
            summaries.append(f"delay: {a['delay']}")
        elif "wait_template" in a:
            summaries.append(f"wait_template: {str(a['wait_template'])[:80]}")
        elif "parallel" in a:
            summaries.append(f"parallel: {len(a['parallel'])} sequences")
        elif "sequence" in a:
            sub = summarise_actions(a["sequence"])
            summaries.extend([f"  {s}" for s in sub])
        else:
            summaries.append(str(a)[:100])
    return summaries


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract devices and study automations from Home Assistant via WebSocket"
    )
    parser.add_argument("--ha-url", required=True, help="e.g. http://localhost:8123")
    parser.add_argument("--token", required=True, help="HA Long-Lived Access Token")
    parser.add_argument("--output-dir", default="configs/bootstrap_snapshot")
    parser.add_argument(
        "--with-devices",
        action="store_true",
        help="Also write ha_devices_with_automations.yaml (debug dump of every HA entity "
             "with a frozen current_state; not read by the agent).",
    )
    args = parser.parse_args()

    out = Path(args.output_dir)

    print(f"\n══ Sherlock Home — HA Extractor (WebSocket) ═════════════════")
    print(f"  HA URL     : {args.ha_url}")
    print(f"  Study tag  : {STUDY_TAG}")
    print(f"  Output dir : {out}\n")

    # ── 1. REST: fetch all entity states
    print("── Step 1: Fetch all entity states (REST) ────────────────────")
    all_states = ha_get(args.ha_url, args.token, "/api/states")
    print(f"  → {len(all_states)} total entities")

    # Index automation states by entity_id
    auto_state_index = {
        s["entity_id"]: s
        for s in all_states
        if s["entity_id"].startswith("automation.")
    }

    # ── 1b. Device registry, so device-targeted actions resolve to real entities.
    # UI-built automations act on a device UUID rather than an entity, so without this
    # an action reads only as "device action: cover.set_position" — with no way to tell
    # which cover. That ambiguity is what let the rule descriptions drift unnoticed.
    print("\n── Step 1b: Fetch device registry (template) ─────────────────")
    global DEVICE_ENTITIES
    DEVICE_ENTITIES = fetch_device_registry(args.ha_url, args.token)
    print(f"  → {len(DEVICE_ENTITIES)} devices mapped to their entities")

    # ── 2. WebSocket: fetch full automation configs
    print("\n── Step 2: Fetch automation configs (WebSocket) ──────────────")
    ws_configs = fetch_automation_configs_via_websocket(args.ha_url, args.token)

    # ── 3. Build study automation entries
    print("\n── Step 3: Build study automation entries ────────────────────")
    study_automations: dict[str, dict] = {}
    entity_to_automations: dict[str, list[str]] = {}

    for study_eid in sorted(STUDY_AUTOMATION_IDS):
        auto_state = auto_state_index.get(study_eid, {})
        attrs = auto_state.get("attributes", {}) if auto_state else {}
        friendly = attrs.get("friendly_name", study_eid)
        enabled = auto_state.get("state") == "on" if auto_state else None
        last_triggered = attrs.get("last_triggered")

        cfg = ws_configs.get(study_eid)

        if cfg:
            # HA 2025.x wraps the actual config inside a nested "config" key
            inner = cfg.get("config", cfg)

            triggers_raw = inner.get("triggers", inner.get("trigger", []))
            conditions_raw = inner.get("conditions", inner.get("condition", []))
            actions_raw = inner.get("actions", inner.get("action", []))

            # Use alias from inner config as friendly name if available
            if inner.get("alias"):
                friendly = inner["alias"]

            involved_entities = extract_entity_ids_from_config(cfg)
            # extract_entity_ids_from_config only sees readable entity ids, so a rule
            # built entirely from device actions used to come out with entities_involved
            # empty — exactly the rules whose targets nobody could check. Fold the
            # registry-resolved devices back in.
            involved_entities = sorted(set(involved_entities) | resolve_config_device_entities(cfg))
            trigger_summary = summarise_triggers(triggers_raw)
            condition_summary = summarise_conditions(conditions_raw)
            action_summary = summarise_actions(actions_raw)

            study_automations[study_eid] = {
                "friendly_name": friendly,
                "entity_id": study_eid,
                "tag": STUDY_TAG,
                "enabled": enabled,
                "last_triggered": last_triggered,
                "trigger_summary": trigger_summary,
                "condition_summary": condition_summary,
                "action_summary": action_summary,
                "entities_involved": involved_entities,
                "raw_config": cfg,
            }

            # Build reverse map: device entity → study automations
            for involved_eid in involved_entities:
                entity_to_automations.setdefault(involved_eid, [])
                if study_eid not in entity_to_automations[involved_eid]:
                    entity_to_automations[involved_eid].append(study_eid)

            print(f"  ✓ {study_eid}")
            for t in trigger_summary:
                print(f"      trigger : {t}")
            for c in condition_summary:
                print(f"      cond    : {c}")
            for a in action_summary:
                print(f"      action  : {a}")
            if involved_entities:
                print(f"      involves: {', '.join(involved_entities)}")

        else:
            print(f"  ⚠ {study_eid} — config not retrieved, state-only entry")
            study_automations[study_eid] = {
                "friendly_name": friendly,
                "entity_id": study_eid,
                "tag": STUDY_TAG,
                "enabled": enabled,
                "last_triggered": last_triggered,
                "note": "Full config not retrieved via WebSocket — check HA UI directly",
            }

    print(f"\n  → {len(study_automations)} study automations processed")
    print(f"  → {len(entity_to_automations)} device entities involved")

    # ── 4. Build device list
    print("\n── Step 4: Build device list ─────────────────────────────────")
    devices: dict[str, dict] = {}
    skipped = 0

    for state in all_states:
        eid = state["entity_id"]
        domain = eid.split(".")[0]

        if domain in SKIP_DOMAINS or domain == "automation":
            skipped += 1
            continue

        entry = parse_device_entry(state)
        involved_in = entity_to_automations.get(eid, [])
        entry["involved_in_study_automations"] = involved_in
        entry["study_automation_count"] = len(involved_in)
        devices[eid] = entry

    print(f"  → {len(devices)} devices (skipped {skipped} internal HA entities)")

    # ── 5. Save outputs
    print("\n── Step 5: Save output files ─────────────────────────────────")

    # The full device dump is OFF by default (--with-devices to write it).
    #
    # It lists every HA entity — including internals the participant never sees — with
    # a `current_state` frozen at generation time. Those frozen values are the problem:
    # they read as truth but go stale immediately, and the agent must get live state
    # from the participant reading the dashboard, never from a snapshot. Everything
    # study-relevant (entity ids, names, rule involvement) already lives in
    # configs/portal/portal_entities.yaml.
    if args.with_devices:
        save_yaml(
            {
                "metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "ha_url": args.ha_url,
                    "total_devices": len(devices),
                    "study_tag": STUDY_TAG,
                    "note": (
                        "DEBUG DUMP — not read by the agent. current_state is frozen at "
                        "generation time and goes stale immediately; never treat it as "
                        "live state. portal_name, portal_locations and diagnostic_note "
                        "are hand-annotated in portal_entities.yaml, not inferrable from HA."
                    ),
                },
                "devices": devices,
            },
            out / "ha_devices_with_automations.yaml",
            label="devices + automation involvement (debug dump)",
        )

    save_yaml(
        {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "ha_url": args.ha_url,
                "study_tag": STUDY_TAG,
                "total_study_automations": len(study_automations),
                "websocket_used": True,
                "note": (
                    f"Only automations in STUDY_AUTOMATION_IDS (tag: {STUDY_TAG}). "
                    "trigger_summary, condition_summary, action_summary are human-readable. "
                    "raw_config contains the full HA automation config as returned by WebSocket."
                ),
            },
            "automations": study_automations,
        },
        out / "ha_study_automations.yaml",
        label="study automations (WebSocket)",
    )

    print(f"\n══ Done ══════════════════════════════════════════════════════")
    print(f"  ha_devices_with_automations.yaml  — all devices + study automation links")
    print(f"  ha_study_automations.yaml         — full configs of {len(study_automations)} study automations\n")


if __name__ == "__main__":
    main()