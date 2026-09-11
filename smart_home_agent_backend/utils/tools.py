from __future__ import annotations

"""
Tools for the smart-home troubleshooting agent.

These tools use YAML configs instead of the old home.json file.
They support environment lookup, device knowledge, visual guidance,
and portal-navigation guidance.

Important:
Visual tools return structured visual_output objects.
The assistant should not paste raw image paths into the reply text.
The frontend should render images from visual_outputs.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain.tools import tool

from smart_home_agent_backend.utils.config_loader import (
    PACKAGE_ROOT,
    PROJECT_ROOT,
    build_compiled_config,
    find_device,
    find_hub,
    get_device_knowledge as lookup_device_knowledge,
    get_portal_entity,
    get_portal_task,
    list_environment_devices,
)


@lru_cache(maxsize=1)
def _get_config() -> dict[str, Any]:
    return build_compiled_config(save_snapshot=False)


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _public_path(path_value: str | None) -> str | None:
    """
    Convert an internal path into a browser-accessible API path.

    Anchors on the "assets" or "outputs" segment and ignores any leading
    package-name prefix, so it works regardless of the package folder name
    (e.g. "smart_home_agent" vs "smart_home_agent_backend") and for both
    relative and absolute inputs.

    Examples:
        smart_home_agent/assets/floorplans/x.png            -> /assets/floorplans/x.png
        C:/.../smart_home_agent_backend/outputs/x.png       -> /outputs/x.png
    """
    if not path_value:
        return None

    parts = Path(str(path_value).replace("\\", "/")).parts

    for anchor in ("assets", "outputs"):
        if anchor in parts:
            idx = parts.index(anchor)
            suffix = "/".join(parts[idx + 1:])
            return f"/{anchor}/{suffix}"

    # fallback, but frontend may not be able to render this
    return str(path_value).replace("\\", "/")


def _room_control_for_device(device: dict[str, Any]) -> dict[str, Any] | None:
    """
    If the device can be operated directly from the room view, return how to
    operate it and what to watch for. Otherwise return None (read its state).

    Controllable: lights, fan, smart plugs, and the TV (toggle); roller shutter
    and curtain (position). Sensors and the heater are not.

    Note on the TV: it is controllable, but it is NOT a diagnostic probe. Its on/off
    command and its reported state travel different paths, and the command is never
    acknowledged — so a TV that does not respond cannot distinguish "command never
    sent" from "command sent, not received". The result is inconclusive, and it must
    never be used to test connectivity or as a comparison device. It can also toggle
    the opposite way if its reported state has drifted.
    """
    device_type = str(device.get("type", "")).lower()

    if device_type == "media":  # the TV
        return {
            "control_kind": "toggle",
            "action": "toggle it on/off using its control",
            "observe": "watch the actual TV and report whether it switched on/off",
            "reliability": (
                "INCONCLUSIVE IF IT DOES NOT RESPOND. The TV's command is not acknowledged "
                "and does not share a path with its reported state, so a non-response proves "
                "nothing about the TV, the hub, or the network. Do not use it to test "
                "connectivity or as a comparison device — use one of the room's lights "
                "instead. If it toggles the opposite way, that is drift, not a fault."
            ),
        }

    if device_type in {"light", "fan", "smart_plug"}:
        return {
            "control_kind": "toggle",
            "action": "toggle it on/off using its control",
            "observe": "watch the actual device in the room and report whether it switched on/off",
        }

    if device_type == "actuator":  # roller shutter / curtain (covers)
        return {
            "control_kind": "position",
            "action": "move it with its open/close or position control",
            "observe": "watch the actual cover in the room and report whether it moved",
        }

    return None


# The model NEVER sees a rule's triggers, conditions or actions — portal_rules.yaml rule
# CONTENTS are deliberately withheld (describing what every rule does would hand over the
# answer to any rule-based scenario). All it ever gets is rule NAMES: globally via
# build_known_automations_block (so it can send the participant straight to a rule by
# name), and per-device here. A name encodes what someone INTENDED — not what it does.
#
# So the model is maximally exposed to the name-trap, by design. "temp_low_heater_on" reads as
# irrelevant on a hot day, and a model with nothing else to go on will skip the one rule that
# holds the answer. Ship the warning WITH the names, wherever they are served, rather than
# relying on a prompt rule far away from the point of use.
_RULES_ATTACHED_WARNING = (
    "These are rule NAMES ONLY. A name is what someone INTENDED the rule to do; its conditions "
    "are what it ACTUALLY does, and the two can say opposite things. You cannot see the "
    "conditions — only the participant can, on the rule's card. So NEVER rule an automation out "
    "because its name sounds unrelated to the symptom: a rule whose name makes it sound "
    "irrelevant is precisely the one to have them open. Judge a rule only after they have read "
    "you its conditions and actions."
)


def _rules_attached_warning(portal_entity: dict[str, Any]) -> dict[str, str]:
    """Attach the name-is-not-behaviour warning wherever rule names are served."""
    if (portal_entity or {}).get("rules_attached"):
        return {"rules_attached_warning": _RULES_ATTACHED_WARNING}
    return {}


def _compact_device(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "aliases": device.get("aliases", []),
        "type": device.get("type"),
        "protocol": device.get("protocol"),
        "hub": device.get("hub"),
        "power": device.get("power"),
        "controls": device.get("controls", {}),
        "location_hint": device.get("location_hint"),
        "floorplan_position_image_path": device.get("floorplan_position_image_path"),
    }


def _compact_hub(hub: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": hub.get("id"),
        "name": hub.get("name"),
        "aliases": hub.get("aliases", []),
        "type": hub.get("type"),
        "protocol": hub.get("protocol"),
        "status": hub.get("status", "unknown"),
        "power": hub.get("power"),
        "location_hint": hub.get("location_hint"),
        "floorplan_position_image_path": hub.get("floorplan_position_image_path"),
    }


@tool
def get_space_info() -> str:
    """Get general information about the smart-home studio space."""
    config = _get_config()
    environment = config["environment"]

    return _json(
        {
            "tool": "get_space_info",
            "home": environment.get("home", {}),
            "space": environment.get("space", {}),
            "floorplan": environment.get("floorplan", {}),
            "network": environment.get("network", {}),
            "number_of_devices": len(environment.get("devices", [])),
            "number_of_hubs": len(environment.get("hubs", [])),
        }
    )


@tool
def list_devices(device_type: str = "") -> str:
    """
    List devices in the smart-home studio.

    Optional device_type examples:
    light, sensor, smart_plug, actuator, thermostat, control_panel.
    """
    config = _get_config()
    environment = config["environment"]

    devices = list_environment_devices(environment)

    if device_type:
        wanted = device_type.strip().lower()
        devices = [d for d in devices if str(d.get("type", "")).lower() == wanted]

    return _json(
        {
            "tool": "list_devices",
            "filter": {"device_type": device_type or None},
            "devices": devices,
            "count": len(devices),
        }
    )


@tool
def get_device_info(device_name_or_id: str) -> str:
    """Get environment information for a device by id, name, or alias."""
    config = _get_config()
    device = find_device(config["environment"], device_name_or_id)

    if not device:
        return _json(
            {
                "tool": "get_device_info",
                "found": False,
                "query": device_name_or_id,
                "message": "No unique device found. Ask the user to clarify or call list_devices.",
            }
        )

    return _json(
        {
            "tool": "get_device_info",
            "found": True,
            "device": _compact_device(device),
        }
    )


@tool
def get_device_knowledge(device_name_or_id: str) -> str:
    """Get device-specific and device-type knowledge."""
    config = _get_config()
    knowledge = lookup_device_knowledge(config, device_name_or_id)

    if not knowledge.get("found"):
        return _json(
            {
                "tool": "get_device_knowledge",
                "found": False,
                "query": device_name_or_id,
                "message": knowledge.get("message", "No matching device knowledge found."),
            }
        )

    return _json(
        {
            "tool": "get_device_knowledge",
            "found": True,
            "device": _compact_device(knowledge.get("device", {})),
            "device_specific_knowledge": knowledge.get("device_specific_knowledge", {}),
            "device_type_knowledge": knowledge.get("device_type_knowledge", {}),
        }
    )


@tool
def check_hub_status(hub_name_or_id: str = "smart_hub") -> str:
    """
    Check hub status from the static environment YAML.

    This is not yet a live Home Assistant API check.
    """
    config = _get_config()
    hub = find_hub(config["environment"], hub_name_or_id)

    if not hub:
        return _json(
            {
                "tool": "check_hub_status",
                "found": False,
                "query": hub_name_or_id,
                "message": "Hub not found in environment config.",
            }
        )

    # Who actually depends on this hub. The agent kept reaching for hub hypotheses to explain
    # Wi-Fi devices, because nothing ever told it which devices are Zigbee. Compute it from
    # the environment rather than restating it in prose, so it cannot drift.
    devices = config["environment"].get("devices", [])
    depends_on_hub = [
        d.get("name") for d in devices if d.get("hub") == hub.get("id")
    ]
    independent = [
        d.get("name") for d in devices
        if d.get("hub") != hub.get("id") and d.get("protocol") != "zigbee"
    ]

    knowledge = lookup_device_knowledge(config, str(hub.get("id", "")))

    return _json(
        {
            "tool": "check_hub_status",
            "found": True,
            "source": "environment_yaml_static_status",
            "hub": _compact_hub(hub),
            "devices_that_depend_on_this_hub": depends_on_hub,
            "devices_that_do_NOT_depend_on_this_hub": independent,
            "scope_rule": (
                "This hub carries Zigbee only. The devices listed as NOT depending on it are "
                "Wi-Fi and do not pass through it at all — a hub fault cannot explain their "
                "behaviour, and their behaviour is not evidence about the hub. A hub "
                "hypothesis is only supported when the devices failing together are all in "
                "the depends-on list."
            ),
            "hub_knowledge": knowledge.get("device_specific_knowledge", {}),
            "note": "Static config status only. Use portal/live API later for real-time status.",
        }
    )


@tool
def check_device_connectivity(device_name_or_id: str) -> str:
    """
    Configuration-based connectivity check.

    It explains protocol, hub dependency, power dependency, and useful next checks.
    """
    config = _get_config()
    environment = config["environment"]
    device = find_device(environment, device_name_or_id)

    if not device:
        return _json(
            {
                "tool": "check_device_connectivity",
                "found": False,
                "query": device_name_or_id,
                "message": "No unique device found. Ask the user to clarify or call list_devices.",
            }
        )

    protocol = str(device.get("protocol", "")).lower()
    hub_id = device.get("hub")
    hub = find_hub(environment, hub_id) if hub_id else None
    hub_status = hub.get("status", "unknown") if hub else None

    checks: list[str] = []

    # Power is assumed good in the study (all devices powered) and is not
    # something the participant can read. Never ask them to check power.
    # Instead separate a device fault from a connectivity issue functionally:
    checks.append(
        "Have the user try operating the device from the dashboard, and from its "
        "physical switch if it has one."
    )
    checks.append(
        "If it responds to neither the dashboard nor a physical switch, treat it "
        "as a device fault."
    )
    checks.append(
        "If it works physically / at the switch but is not reachable from the "
        "dashboard, treat it as a connectivity issue."
    )

    if protocol == "zigbee":
        checks.extend(
            [
                "Check whether the Smart Hub is online.",
                "Compare with another Zigbee device.",
                "If only this device fails, consider range, pairing, or device-specific issues.",
            ]
        )
    elif protocol == "wifi":
        checks.extend(
            [
                "Check whether the device/tablet is connected to Wi-Fi.",
                "Compare with another Wi-Fi-controlled device.",
                "Check portal state because Wi-Fi devices may show stale or unavailable states.",
            ]
        )
    else:
        checks.extend(
            [
                "Check whether the device appears in the portal.",
                "Compare with a similar device.",
            ]
        )

    if hub and str(hub_status).lower() != "online":
        assessment = f"The device depends on {hub.get('name')}, and the hub is {hub_status}."
    elif hub:
        assessment = f"The device depends on {hub.get('name')}, which is listed as {hub_status}."
    else:
        assessment = "No hub dependency is configured. Focus on Wi-Fi/Zigbee reachability, portal state, and UI mapping."

    return _json(
        {
            "tool": "check_device_connectivity",
            "found": True,
            "source": "configuration_based_check_not_live_state",
            "device": _compact_device(device),
            "hub": _compact_hub(hub) if hub else None,
            "likely_assessment": assessment,
            "recommended_next_checks": checks,
        }
    )


@tool
def show_device_location(device_name_or_id: str) -> str:
    """
    Return the prepared floorplan image path for a device.

    The returned path is browser-accessible, e.g. /assets/floorplans/...
    """
    config = _get_config()
    device = find_device(config["environment"], device_name_or_id)

    if not device:
        return _json(
            {
                "tool": "show_device_location",
                "found": False,
                "query": device_name_or_id,
                "message": "No unique device found. Ask the user to clarify or call list_devices.",
            }
        )

    configured_path = device.get("floorplan_position_image_path")
    absolute_path = device.get("floorplan_position_image_abs_path")

    public_path = _public_path(absolute_path or configured_path)
    exists = bool(absolute_path and Path(absolute_path).exists())

    return _json(
        {
            "tool": "show_device_location",
            "found": True,
            "device": {
                "id": device.get("id"),
                "name": device.get("name"),
                "location_hint": device.get("location_hint"),
            },
            "visual_output": {
                "type": "floorplan_position_image",
                "path": public_path,
                "configured_path": configured_path,
                "absolute_path": absolute_path,
                "exists": exists,
                "related_device_id": device.get("id"),
                "caption": (
                    f"{device.get('name')} is located at: "
                    f"{device.get('location_hint')}."
                ),
            },
            "user_instruction": (
                f"I marked {device.get('name')} on the floorplan. "
                f"It is located at: {device.get('location_hint')}."
            ),
        }
    )


@tool
def show_hub_location(hub_name_or_id: str = "smart_hub") -> str:
    """Return the prepared floorplan image path for a hub."""
    config = _get_config()
    hub = find_hub(config["environment"], hub_name_or_id)

    if not hub:
        return _json(
            {
                "tool": "show_hub_location",
                "found": False,
                "query": hub_name_or_id,
                "message": "Hub not found.",
            }
        )

    configured_path = hub.get("floorplan_position_image_path")
    absolute_path = hub.get("floorplan_position_image_abs_path")

    public_path = _public_path(absolute_path or configured_path)
    exists = bool(absolute_path and Path(absolute_path).exists())

    return _json(
        {
            "tool": "show_hub_location",
            "found": True,
            "hub": {
                "id": hub.get("id"),
                "name": hub.get("name"),
                "location_hint": hub.get("location_hint"),
            },
            "visual_output": {
                "type": "floorplan_position_image",
                "path": public_path,
                "configured_path": configured_path,
                "absolute_path": absolute_path,
                "exists": exists,
                "related_device_id": hub.get("id"),
                "caption": (
                    f"{hub.get('name')} is located at: "
                    f"{hub.get('location_hint')}."
                ),
            },
            "user_instruction": (
                f"I marked {hub.get('name')} on the floorplan. "
                f"It is located at: {hub.get('location_hint')}."
            ),
        }
    )


@tool
def get_portal_overview() -> str:
    """Get the high-level structure of the smart-home management portal."""
    config = _get_config()

    return _json(
        {
            "tool": "get_portal_overview",
            "found": bool(config.get("portal_structure")),
            "portal_structure": config.get("portal_structure", {}),
        }
    )


@tool
def list_portal_tasks() -> str:
    """List available portal diagnostic tasks."""
    config = _get_config()
    tasks = config.get("portal_tasks", {}).get("tasks", {})

    return _json(
        {
            "tool": "list_portal_tasks",
            "tasks": list(tasks.keys()),
            "count": len(tasks),
        }
    )


@tool
def get_portal_task_guide(task_name: str) -> str:
    """Get a portal navigation guide for a diagnostic task."""
    config = _get_config()
    task = get_portal_task(config, task_name)

    return _json(
        {
            "tool": "get_portal_task_guide",
            **task,
        }
    )


@tool
def get_portal_entity_info(device_name_or_id: str) -> str:
    """Get portal/Home Assistant entity mapping for a device."""
    config = _get_config()
    entity = get_portal_entity(config, device_name_or_id)

    payload: dict[str, Any] = {"tool": "get_portal_entity_info", **entity}

    # A rule NAME is the only thing this tool can tell the model about a rule — the rule's
    # actual triggers, conditions and actions are deliberately withheld (portal_rules.yaml is
    # INTENTIONALLY_UNREAD, because describing what every rule does would hand over the answer
    # to any rule-based scenario). So the model's entire model of a rule is a string that
    # encodes what someone MEANT it to do.
    #
    # That is exactly the trap: a rule called "temp_low_heater_on" reads as irrelevant on a hot
    # day, and a model with nothing else to go on will skip it. Serve the warning WITH the name,
    # every time, rather than relying on a prompt rule forty lines away.
    if (entity.get("portal_entity") or {}).get("rules_attached"):
        payload["rules_attached_warning"] = _RULES_ATTACHED_WARNING

    return _json(payload)


@tool
def get_device_portal_check_guide(device_name_or_id: str) -> str:
    """
    Return a concrete portal check guide for a device.

    This tells the user where to go in the portal and what values to report.
    """
    config = _get_config()
    entity = get_portal_entity(config, device_name_or_id)

    if not entity.get("found"):
        return _json(
            {
                "tool": "get_device_portal_check_guide",
                "found": False,
                "query": device_name_or_id,
                "message": entity.get("message", "No portal entity found."),
            }
        )

    device = entity["device"]
    portal_entity = entity["portal_entity"]
    portal_name = portal_entity.get("portal_name", device.get("name"))

    # Fast functional check: if the device can be operated from the room view,
    # prefer commanding it and watching the room over reading a State field. It
    # is quicker for the participant and directly proves the command reaches the
    # device. The state-read stays available as a fallback / for exact values.
    room_control = _room_control_for_device(device)
    if room_control:
        return _json(
            {
                "tool": "get_device_portal_check_guide",
                "found": True,
                "device": _compact_device(device),
                "portal_entity": portal_entity,
                **_rules_attached_warning(portal_entity),
                "portal_instruction": {
                    "main_goal": "Quickly test whether the device still responds by operating it and watching the room.",
                    "check_kind": "functional_responsiveness",
                    "recommended_tab": "Your Room",
                    "steps": [
                        "Open the dashboard.",
                        "Go to Your Room.",
                        f"Find {portal_name} (its tile in the room cards, or its icon on the floor map).",
                        f"Then {room_control['action']}.",
                        f"Now {room_control['observe']}.",
                    ],
                    "user_should_report": [
                        "whether the device physically responded (e.g. light turned on, cover moved)",
                        "or whether nothing happened",
                    ],
                    "fallback_if_exact_value_needed": {
                        "main_goal": "If you need the exact reported value instead, read it in All Devices.",
                        "recommended_tab": "All Devices",
                        "user_should_report": [
                            "State",
                            "Last changed",
                            "Any warning/unavailable/offline indicator",
                        ],
                    },
                },
            }
        )

    return _json(
        {
            "tool": "get_device_portal_check_guide",
            "found": True,
            "device": _compact_device(device),
            "portal_entity": portal_entity,
            **_rules_attached_warning(portal_entity),
            "portal_instruction": {
                "main_goal": "Collect actual technical state from the dashboard.",
                "recommended_tab": portal_entity.get("tab", "All Devices"),
                "steps": [
                    "Open the dashboard.",
                    f"Go to {portal_entity.get('tab', 'All Devices')}.",
                    f"Find {portal_name}.",
                    "Read the State value.",
                    "Read Last changed.",
                    "Tell the agent exactly what values are shown.",
                ],
                "user_should_report": [
                    "State",
                    "Last changed",
                    "Any warning/unavailable/offline indicator",
                ],
                # All Devices shows Device / Category / State / Last changed. There is
                # no Parameters or Rules Attached COLUMN — a device's attached rules
                # live in its detail card, opened by tapping the row.
                "attached_rules_note": (
                    "To see which rules touch this device, tap its row to open its "
                    "detail card and read the 'Attached Rules' list. Its physical "
                    "switch, if it has one, is listed separately under "
                    "All Rules > Physical switches."
                ),
            },
        }
    )


if __name__ == "__main__":
    print(get_space_info.invoke({}))
    print(list_devices.invoke({}))
    print(get_device_info.invoke({"device_name_or_id": "Door Light"}))
    print(show_device_location.invoke({"device_name_or_id": "Door Light"}))
    print(get_device_portal_check_guide.invoke({"device_name_or_id": "Door Light"}))