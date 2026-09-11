"""
Loads all YAML config files for the smart-home troubleshooting agent.

This includes the environment, behavior rules, diagnostic framework,
device knowledge, tool policy, portal structure, portal entities,
portal tasks, portal rules, and optional scenario files.

It can also save a compiled version of the loaded configs in
configs/compiled/ so I can later check exactly what the agent used
for a specific scenario or experiment run.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # smart_home_agent/
PROJECT_ROOT = PACKAGE_ROOT.parent
CONFIG_ROOT = PACKAGE_ROOT / "configs"

DEFAULT_PATHS = {
    "environment": CONFIG_ROOT / "environment" / "Offis_smart_studio.yaml",
    "behavior": CONFIG_ROOT / "behaviors" / "guided_diagnostic_behavior.yaml",
    "foundation": CONFIG_ROOT / "foundation" / "diagnostic_framework.yaml",
    "knowledge": CONFIG_ROOT / "knowledge" / "device_knowledge.yaml",
    "tool_policy": CONFIG_ROOT / "tools" / "tool_policy.yaml",
    "portal_structure": CONFIG_ROOT / "portal" / "portal_structure.yaml",
    "portal_entities": CONFIG_ROOT / "portal" / "portal_entities.yaml",
    "portal_tasks": CONFIG_ROOT / "portal" / "portal_tasks.yaml",
    "portal_rules": CONFIG_ROOT / "portal" / "portal_rules.yaml",
    "portal_conditions": CONFIG_ROOT / "portal" / "portal_conditions.yaml",
}

SCENARIO_DIR = CONFIG_ROOT / "reasoning-model_scenarios"
COMPILED_DIR = CONFIG_ROOT / "compiled"


def load_yaml(path: str | Path, required: bool = True) -> dict[str, Any]:
    path = Path(path)

    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

    if not path.exists():
        if required:
            raise FileNotFoundError(f"YAML config file not found: {path}")
        return {}

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a dictionary: {path}")

    return data


def save_yaml(data: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)

    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            data,
            file,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    return path


def normalize_path_string(path_value: str) -> str:
    return path_value.replace("\\", "/")


def resolve_project_path(path_value: str | Path) -> Path:
    path = Path(normalize_path_string(str(path_value)))

    if path.is_absolute():
        return path.resolve()

    # Anchor known asset roots under the package directory, ignoring any
    # leading package-name prefix in the configured path. This keeps the
    # configs working regardless of what the package folder is named
    # (e.g. "smart_home_agent" vs "smart_home_agent_backend").
    parts = path.parts
    for anchor in ("assets", "outputs"):
        if anchor in parts:
            idx = parts.index(anchor)
            return PACKAGE_ROOT.joinpath(*parts[idx:]).resolve()

    return (PROJECT_ROOT / path).resolve()


def normalize_environment_paths(environment: dict[str, Any]) -> dict[str, Any]:
    env = deepcopy(environment)

    floorplan = env.get("floorplan", {})
    if isinstance(floorplan, dict):
        base_path = floorplan.get("base_image_path")
        if isinstance(base_path, str):
            floorplan["base_image_path"] = normalize_path_string(base_path)
            floorplan["base_image_abs_path"] = str(resolve_project_path(base_path))
        env["floorplan"] = floorplan

    for section in ["devices", "hubs"]:
        for item in env.get(section, []):
            if not isinstance(item, dict):
                continue

            img_path = item.get("floorplan_position_image_path")
            if isinstance(img_path, str):
                item["floorplan_position_image_path"] = normalize_path_string(img_path)
                item["floorplan_position_image_abs_path"] = str(resolve_project_path(img_path))

    return env


def load_base_configs(
    environment_path: str | Path | None = None,
    behavior_path: str | Path | None = None,
    foundation_path: str | Path | None = None,
    knowledge_path: str | Path | None = None,
    tool_policy_path: str | Path | None = None,
    portal_structure_path: str | Path | None = None,
    portal_entities_path: str | Path | None = None,
    portal_tasks_path: str | Path | None = None,
    portal_rules_path: str | Path | None = None,
    portal_conditions_path: str | Path | None = None,
) -> dict[str, Any]:
    environment = load_yaml(environment_path or DEFAULT_PATHS["environment"])
    environment = normalize_environment_paths(environment)

    return {
        "environment": environment,
        "behavior": load_yaml(behavior_path or DEFAULT_PATHS["behavior"]),
        "foundation": load_yaml(foundation_path or DEFAULT_PATHS["foundation"]),
        # Device knowledge is optional: the file may be absent while the
        # knowledge base is being reworked. Tools degrade gracefully to {}.
        "knowledge": load_yaml(knowledge_path or DEFAULT_PATHS["knowledge"], required=False),
        "tool_policy": load_yaml(tool_policy_path or DEFAULT_PATHS["tool_policy"]),
        "portal_structure": load_yaml(
            portal_structure_path or DEFAULT_PATHS["portal_structure"],
            required=False,
        ),
        "portal_entities": load_yaml(
            portal_entities_path or DEFAULT_PATHS["portal_entities"],
            required=False,
        ),
        "portal_tasks": load_yaml(
            portal_tasks_path or DEFAULT_PATHS["portal_tasks"],
            required=False,
        ),
        "portal_rules": load_yaml(
            portal_rules_path or DEFAULT_PATHS["portal_rules"],
            required=False,
        ),
        "portal_conditions": load_yaml(
            portal_conditions_path or DEFAULT_PATHS["portal_conditions"],
            required=False,
        ),
    }


def load_scenario(
    scenario_id: str | None = None,
    scenario_path: str | Path | None = None,
) -> dict[str, Any] | None:
    if scenario_path:
        return load_yaml(scenario_path)

    if not scenario_id:
        return None

    return load_yaml(SCENARIO_DIR / f"{scenario_id}.yaml")


def build_compiled_config(
    scenario_id: str | None = None,
    scenario_path: str | Path | None = None,
    save_snapshot: bool = False,
    snapshot_name: str | None = None,
    **paths: Any,
) -> dict[str, Any]:
    compiled = load_base_configs(**paths)

    scenario = load_scenario(scenario_id=scenario_id, scenario_path=scenario_path)
    compiled["scenario"] = scenario

    compiled["runtime_metadata"] = {
        "compiled_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "package_root": str(PACKAGE_ROOT),
        "scenario_id": scenario_id or extract_scenario_id(scenario),
    }

    validate_compiled_config(compiled)

    if save_snapshot:
        snapshot_path = get_snapshot_path(
            scenario_id=scenario_id or extract_scenario_id(scenario),
            snapshot_name=snapshot_name,
        )
        save_yaml(compiled, snapshot_path)

    return compiled


def get_snapshot_path(
    scenario_id: str | None = None,
    snapshot_name: str | None = None,
) -> Path:
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)

    if snapshot_name:
        filename = snapshot_name if snapshot_name.endswith(".yaml") else f"{snapshot_name}.yaml"
    else:
        # The "snapshot_" prefix keeps these distinguishable from the hand-written
        # scenario configs, and the timestamp keeps one file per run.
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"snapshot_{scenario_id or 'no_scenario'}_{timestamp}.yaml"

    return COMPILED_DIR / filename


def extract_scenario_id(scenario: dict[str, Any] | None) -> str | None:
    if not scenario:
        return None

    metadata = scenario.get("metadata", {})
    if isinstance(metadata, dict):
        scenario_id = metadata.get("scenario_id")
        if isinstance(scenario_id, str):
            return scenario_id

    return None


def validate_compiled_config(compiled: dict[str, Any]) -> None:
    required = ["environment", "behavior", "foundation", "tool_policy"]

    for key in required:
        if key not in compiled or not isinstance(compiled[key], dict):
            raise ValueError(f"Compiled config missing required section: {key}")

    validate_environment(compiled["environment"])

    # Knowledge is optional; only validate its shape when it is present.
    if compiled.get("knowledge"):
        validate_knowledge(compiled["knowledge"])


def validate_environment(environment: dict[str, Any]) -> None:
    if "devices" not in environment or not isinstance(environment["devices"], list):
        raise ValueError("Environment config must contain top-level 'devices' list.")

    if "hubs" not in environment or not isinstance(environment["hubs"], list):
        raise ValueError("Environment config must contain top-level 'hubs' list.")

    seen: set[str] = set()

    for device in environment["devices"]:
        for field in ["id", "name", "type"]:
            if not device.get(field):
                raise ValueError(f"Device missing field '{field}': {device}")

        if device["id"] in seen:
            raise ValueError(f"Duplicate device id: {device['id']}")

        seen.add(device["id"])


def validate_knowledge(knowledge: dict[str, Any]) -> None:
    if "devices" not in knowledge:
        raise ValueError("device_knowledge.yaml must contain 'devices'.")

    if "device_type_knowledge" not in knowledge:
        raise ValueError("device_knowledge.yaml must contain 'device_type_knowledge'.")


def normalize_text(value: str) -> str:
    return (
        value.lower()
        .strip()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("/", "_")
    )


def _matches_query(item: dict[str, Any], query: str) -> bool:
    q = normalize_text(query)

    candidates = [
        str(item.get("id", "")),
        str(item.get("name", "")),
    ]

    aliases = item.get("aliases", [])
    if isinstance(aliases, list):
        candidates.extend(str(alias) for alias in aliases)

    return any(q == normalize_text(candidate) for candidate in candidates)


def _contains_query(item: dict[str, Any], query: str) -> bool:
    q = normalize_text(query)

    candidates = [
        str(item.get("id", "")),
        str(item.get("name", "")),
    ]

    aliases = item.get("aliases", [])
    if isinstance(aliases, list):
        candidates.extend(str(alias) for alias in aliases)

    return any(q and q in normalize_text(candidate) for candidate in candidates)


# ── Resolving a device from what someone actually typed ───────────────────────────────
#
# `_contains_query` asks: "is the whole user utterance a substring of the device's name?"
# That is BACKWARDS, and it is why two pilot sessions stalled:
#
#   "The door light, like I already said"  -> UNRESOLVED. The agent re-issued the identical
#       five-option disambiguation prompt. Twice. The participant had to strip their sentence
#       down to the bare words "Door Light" before it would take.
#   "bed light R"  -> UNRESOLVED, and the agent listed all five lights — including ones the
#       participant had plainly excluded.
#
# Even "the fan" failed. Any extra word at all was fatal, because the test only ever passes
# when the user types the device's name and NOTHING else.
#
# The right question is the other way round: DOES THE DEVICE'S NAME APPEAR IN WHAT THEY SAID?
# And it has to survive spacing, since "bed light r", "bedlight r" and "BedLight_R" are the
# same thing to everyone except a string comparison.

def _squash(text: str) -> str:
    """Strip to bare alphanumerics: 'Bed light R' and 'BedLight_R' both -> 'bedlightr'."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _labels(item: dict[str, Any]) -> list[str]:
    labels = [str(item.get("id", "")), str(item.get("name", ""))]
    aliases = item.get("aliases", [])
    if isinstance(aliases, list):
        labels += [str(a) for a in aliases]
    return [l for l in labels if l.strip()]


def _mention_score(item: dict[str, Any], text: str) -> int:
    """How strongly this device is named in `text`. 0 = not mentioned.

    The score is the LENGTH of the longest label that matched, so a specific name beats a
    generic one: "Bedlight Right" (13) outranks the bare alias "Fan" (3), and "Fan socket" (9)
    outranks "Fan" (3) when someone says "the fan socket".
    """
    squashed = _squash(text)
    tokens = set(re.findall(r"[a-z0-9]+", str(text or "").lower()))
    best = 0

    for label in _labels(item):
        s = _squash(label)
        if not s:
            continue
        # Short labels ("TV") must match a whole word — otherwise they hit inside other words.
        if len(s) < 4:
            if s in tokens:
                best = max(best, len(s))
        elif s in squashed:
            best = max(best, len(s))

    return best


def _mentioned_devices(devices: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Devices named in the text, best match first."""
    scored = [(d, _mention_score(d, text)) for d in devices]
    hits = [(d, s) for d, s in scored if s > 0]
    if not hits:
        return []
    top = max(s for _, s in hits)
    return [d for d, s in hits if s == top]


def find_device(environment: dict[str, Any], device_query: str) -> dict[str, Any] | None:
    devices = environment.get("devices", [])

    # 1. An exact id/name/alias — how the tools address a device internally.
    exact = [device for device in devices if _matches_query(device, device_query)]
    if len(exact) == 1:
        return exact[0]

    # 2. A device NAMED somewhere in free text. This is the human path, and the one that
    #    was broken: "the door light, like I already said" now resolves on the first try.
    mentioned = _mentioned_devices(devices, device_query)
    if len(mentioned) == 1:
        return mentioned[0]

    # 3. A partial identifier typed on its own ("temperaturesens" -> one hit). ONLY for a
    #    single bare token: this step asks "is the query a substring of a device label", which
    #    is a reasonable question about a half-typed id and a terrible one about a sentence.
    #    Applied to free text it invents confidence — "the light" matched Door Light, purely
    #    because it is a prefix of that device's alias "the light next to the door", when the
    #    participant could have meant any of five lights. Ambiguity must stay ambiguous.
    if not re.search(r"\s", str(device_query or "").strip()):
        loose = [device for device in devices if _contains_query(device, device_query)]
        if len(loose) == 1:
            return loose[0]

    return None


def find_device_candidates(environment: dict[str, Any], device_query: str) -> list[dict[str, Any]]:
    """The devices worth disambiguating between — NARROWED, never the whole room.

    "bed light R" used to return nothing, so the caller fell back to listing all five lights,
    including the four the participant had just ruled out. Now it returns exactly what they
    could have meant.
    """
    devices = environment.get("devices", [])

    mentioned = _mentioned_devices(devices, device_query)
    if mentioned:
        return mentioned

    if not re.search(r"\s", str(device_query or "").strip()):
        loose = [device for device in devices if _contains_query(device, device_query)]
        if loose:
            return loose

    # Nothing named outright — fall back to shared words ("the light" -> the five lights, not
    # the whole room). Ignore words that are common to everything.
    stop = {"the", "a", "an", "my", "is", "not", "on", "off", "it", "this", "that", "smart", "device"}
    words = {w for w in re.findall(r"[a-z]{3,}", str(device_query or "").lower()) if w not in stop}
    if not words:
        return []

    overlap = []
    for device in devices:
        label_words = {
            w for label in _labels(device) for w in re.findall(r"[a-z]{3,}", label.lower())
        }
        if words & label_words:
            overlap.append(device)
    return overlap


def find_hub(environment: dict[str, Any], hub_query: str) -> dict[str, Any] | None:
    hubs = environment.get("hubs", [])

    exact = [hub for hub in hubs if _matches_query(hub, hub_query)]
    if len(exact) == 1:
        return exact[0]

    loose = [hub for hub in hubs if _contains_query(hub, hub_query)]
    if len(loose) == 1:
        return loose[0]

    return None


def list_environment_devices(environment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": device.get("id"),
            "name": device.get("name"),
            "type": device.get("type"),
            "protocol": device.get("protocol"),
            "hub": device.get("hub"),
            "power": device.get("power"),
            "location_hint": device.get("location_hint"),
            "aliases": device.get("aliases", []),
        }
        for device in environment.get("devices", [])
    ]


def get_device_knowledge(compiled: dict[str, Any], device_query: str) -> dict[str, Any]:
    environment = compiled["environment"]
    knowledge = compiled["knowledge"]

    device = find_device(environment, device_query)

    # The hub lives in environment.hubs, not environment.devices, so find_device never
    # matched it — which meant device_knowledge.devices.smart_hub was unreachable by ANY
    # path, and the agent could not learn the one fact that decides most connectivity
    # questions here: the hub serves Zigbee only, so the Wi-Fi devices (Smart Fan, TV) do
    # not depend on it at all.
    if not device:
        hub = find_hub(environment, device_query)
        if hub:
            device = hub

    if not device:
        return {
            "found": False,
            "message": f"No device found for query: {device_query}",
        }

    device_id = device.get("id")
    device_type = device.get("type")

    return {
        "found": True,
        "device": device,
        "device_specific_knowledge": knowledge.get("devices", {}).get(device_id, {}),
        "device_type_knowledge": knowledge.get("device_type_knowledge", {}).get(device_type, {}),
    }


def get_portal_entity(compiled: dict[str, Any], device_query: str) -> dict[str, Any]:
    environment = compiled["environment"]
    portal_entities = compiled.get("portal_entities", {})

    device = find_device(environment, device_query)
    if not device:
        return {
            "found": False,
            "message": f"No environment device found for query: {device_query}",
        }

    device_id = device.get("id")
    entities = portal_entities.get("devices", {})

    entity_info = entities.get(device_id)
    if not entity_info:
        return {
            "found": False,
            "device": device,
            "message": f"No portal entity mapping found for device id: {device_id}",
        }

    return {
        "found": True,
        "device": device,
        "portal_entity": entity_info,
    }


def get_portal_task(compiled: dict[str, Any], task_name: str) -> dict[str, Any]:
    portal_tasks = compiled.get("portal_tasks", {})
    tasks = portal_tasks.get("tasks", {})

    normalized = normalize_text(task_name)

    for key, task in tasks.items():
        if normalize_text(key) == normalized:
            return {"found": True, "task_id": key, "task": task}

    for key, task in tasks.items():
        if normalized in normalize_text(key):
            return {"found": True, "task_id": key, "task": task}

    return {
        "found": False,
        "message": f"No portal task found for: {task_name}",
    }


def bullet_list(items: list[Any], indent: int = 0) -> str:
    prefix = " " * indent
    return "\n".join(f"{prefix}- {item}" for item in items)


def _clean_scalar(value: Any) -> str:
    """Collapse a YAML folded/literal scalar to a single trimmed line."""
    return " ".join(str(value or "").split())


def get_condition_config(
    portal_conditions: dict[str, Any],
    presentation_mode: str,
) -> dict[str, Any]:
    """Return the condition block for the active presentation_mode.

    Falls back to the "dashboard" block, then to {}, so a missing config never
    breaks prompt assembly.
    """
    conditions = (portal_conditions or {}).get("conditions", {}) or {}
    return conditions.get(presentation_mode) or conditions.get("dashboard") or {}


def build_time_model_block(environment: dict[str, Any]) -> str:
    """Which of the lab's three clocks counts as "now" — and what to call it out loud.

    Every time-gated rule in the study is decided by input_datetime.experiment_clock, which
    is what the dashboard's clock pill shows. The physical clock in the room is a different
    helper, and real wall-clock time is a third thing that agrees with neither. An agent
    reasoning about the wrong "now" will confidently rule out the correct rule.

    The naming half is not cosmetic. Telling a participant the clock is simulated reveals
    the apparatus and turns them from someone debugging a smart home into someone debugging
    an experiment.
    """
    tm = (environment or {}).get("time_model", {}) or {}
    if not tm:
        return ""

    lines = ["Time — there are three clocks here, and they do not have to agree:"]
    for key in ("authoritative", "lab_clock", "real_time"):
        block = tm.get(key) or {}
        if block.get("what"):
            # Render the human label, never the HA entity id — see the note in time_model.
            label = block.get("label") or key.replace("_", " ")
            lines.append(f"- {label}: {_clean_scalar(block['what'])}")

    for rule in tm.get("rules", []) or []:
        lines.append(f"- {_clean_scalar(rule)}")

    # Render ONLY the positive naming rule. `never_say` and `why` are deliberately NOT
    # rendered: a prohibition cannot be written without naming the thing it prohibits, and
    # naming it ("never call it the simulated/fake clock", "that would reveal the apparatus",
    # "it tells them they are in a study") told the MODEL that the clock is staged and that
    # this is an experiment. That is the apparatus, handed to the agent in the course of
    # trying to stop the agent from handing it to the participant — and for the scenario
    # whose answer IS a wrong clock, it is most of the way to the answer. See the note in
    # environment.time_model.participant_facing_language.
    lang = tm.get("participant_facing_language") or {}
    say = lang.get("say") or []
    instruction = _clean_scalar(lang.get("prompt_instruction"))
    if instruction:
        names = ", ".join(f'"{s}"' for s in say)
        lines.append(f"- {instruction}" + (f' Use: {names}.' if names else ""))

    return "\n".join(lines)


def build_shared_portal_block(portal_conditions: dict[str, Any]) -> str:
    """The parts of the dashboard that are identical in BOTH presentation conditions.

    Was loaded and never read. Its absence mattered: nothing in the prompt told the model
    that a wall switch is READ-ONLY and appears nowhere in All Devices, so the agent could
    cheerfully send a participant to "toggle the switch on the dashboard" — a control that
    does not exist in either build.
    """
    shared = (portal_conditions or {}).get("shared", {}) or {}
    if not shared:
        return ""

    lines = ["The dashboard, in both conditions (identical for every participant):"]

    nav = shared.get("nav_rail") or []
    if nav:
        lines.append(f"- Navigation: {' | '.join(str(n) for n in nav)}")

    for key in ("all_devices", "all_rules"):
        block = shared.get(key) or {}
        if not block:
            continue
        lines.append(f"- {block.get('label', key)}: {_clean_scalar(block.get('purpose'))}")
        for section in block.get("sections", []) or []:
            lines.append(f"  - {_clean_scalar(section)}")

    switches = shared.get("physical_switches") or {}
    if switches:
        lines.append("- Physical switches (wall switches and buttons):")
        for key in ("read_only", "where_they_appear", "where_they_do_NOT_appear", "diagnostic_use"):
            if switches.get(key):
                lines.append(f"  - {_clean_scalar(switches[key])}")

    pills = shared.get("filter_pills") or []
    if pills:
        lines.append(f"- Category filters available: {', '.join(str(p) for p in pills)}")

    return "\n".join(lines)


def build_task_selection_block(portal_tasks: dict[str, Any]) -> str:
    """Which check to reach for — the policy that decides efficiency.

    Was loaded and never read, which meant the model could list the portal tasks and fetch
    any one of them, but was given nothing about WHICH to pick. Since the study measures
    how efficiently the agent narrows a problem down, the rule for choosing the next check
    is close to the whole game.
    """
    policy = (portal_tasks or {}).get("task_selection_policy", {}) or {}
    if not policy:
        return ""

    lines = ["Choosing the next check (prefer the one that settles the most):"]
    for name, guidance in policy.items():
        lines.append(f"- {name}: {_clean_scalar(guidance)}")
    return "\n".join(lines)


def build_condition_guidance(
    portal_conditions: dict[str, Any],
    presentation_mode: str,
) -> str:
    """Build the 'Presentation condition' prompt block for the active build.

    The two portal builds differ only in the Your Room tab, so this block tells
    the model exactly how that participant's room view looks and the precise
    breadcrumbs to use for operating a device, reading its state, and (only where
    the build has them) checking connectivity/dependency graphs.
    """
    condition = get_condition_config(portal_conditions, presentation_mode)
    if not condition:
        return ""

    lines = [
        "Presentation condition (how THIS participant's dashboard actually looks — "
        "keep every navigation breadcrumb loyal to it):",
        f"- condition: {condition.get('display_name', presentation_mode)} "
        f"({condition.get('id', presentation_mode)})",
        f"- Your Room view: {_clean_scalar(condition.get('room_view_summary'))}",
        f"- To operate/test a device: {_clean_scalar(condition.get('operate_device'))}",
        f"- To read a device's exact State: "
        f"{_clean_scalar(condition.get('inspect_exact_values'))}",
        f"- What to CALL a device to the participant: "
        f"{_clean_scalar(condition.get('device_label'))}",
        f"- To check connectivity: {_clean_scalar(condition.get('connectivity_check'))}",
        f"- To check automation dependencies: {_clean_scalar(condition.get('dependency_check'))}",
    ]

    constraints = condition.get("constraints", []) or []
    if constraints:
        lines.append("- Hard constraints for this condition:")
        lines.append(bullet_list([_clean_scalar(c) for c in constraints], indent=2))

    return "\n".join(lines)


# How many items of each running-state list to surface in the prompt. Older
# items stay in the message history if the model truly needs them; this keeps
# the "Conversation state" block from growing unbounded over a long session.
_STATE_BLOCK_CAP = 8


def _last_n(items: list[Any], n: int = _STATE_BLOCK_CAP) -> list[Any]:
    items = items or []
    return items[-n:]


def _short(value: Any, limit: int = 120) -> str:
    """One trimmed line, truncated, for compact state rendering."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_conversation_state_block(state: dict[str, Any]) -> str:
    """Render what has already been checked/confirmed so the model stops asking
    for it again. This is the Phase 1 grounding fix: SmartHomeState already
    tracks these, but nothing rendered them into the prompt.
    """
    state = state or {}
    lines: list[str] = []

    checked_devices = _last_n(state.get("checked_devices", []))
    if checked_devices:
        lines.append("Already-checked devices (do not re-investigate unless something changed):")
        lines.append(bullet_list([_short(d) for d in checked_devices], indent=2))

    checked_tools = _last_n(state.get("checked_tools", []))
    if checked_tools:
        lines.append("Tool checks already run this session:")
        tool_lines = []
        for record in checked_tools:
            if isinstance(record, dict):
                name = record.get("tool_name", "tool")
                args = record.get("tool_args") or {}
                result = record.get("tool_result", "")
                tool_lines.append(_short(f"{name}({args}) -> {result}"))
            else:
                tool_lines.append(_short(record))
        lines.append(bullet_list(tool_lines, indent=2))

    active_hypotheses = [
        h for h in state.get("hypotheses", []) or []
        if isinstance(h, dict) and h.get("status") not in {"rejected", "resolved"}
    ]
    # Ranked board (Phase 10): show each surviving cause WITH the cheapest check that
    # would settle it, in rank order, so the model reaches for rank 1 next instead of
    # improvising. The discriminating_check is the whole point — a cause without its
    # test is just a guess.
    active_hypotheses = sorted(
        _last_n(active_hypotheses), key=lambda h: h.get("rank", 99)
    )
    if active_hypotheses:
        lines.append(
            "Ranked hypothesis board (test rank 1 next — the cheapest check that settles the "
            "most; then re-rank as answers land):"
        )
        hyp_lines = []
        for h in active_hypotheses:
            rank = h.get("rank", "?")
            desc = h.get("description", "")
            status = h.get("status", "untested")
            check = h.get("discriminating_check", "")
            line = f"#{rank} [{status}] {desc}"
            if check:
                line += f"  — check: {check}"
            hyp_lines.append(_short(line, limit=200))
        lines.append(bullet_list(hyp_lines, indent=2))

    rejected = _last_n(state.get("rejected_hypotheses", []))
    if rejected:
        lines.append("Ruled-OUT causes (their check came back against them — do NOT re-suggest or re-check):")
        rej_lines = []
        for h in rejected:
            if isinstance(h, dict):
                rej_lines.append(_short(f"{h.get('description', '')}"))
            else:
                rej_lines.append(_short(h))
        lines.append(bullet_list(rej_lines, indent=2))

    confirmed = _last_n(state.get("confirmed_facts", []))
    if confirmed:
        lines.append("Confirmed facts so far:")
        fact_lines = []
        for f in confirmed:
            if isinstance(f, dict):
                src = f.get("source", "?")
                fact_lines.append(_short(f"{f.get('content', '')} (source: {src})"))
            else:
                fact_lines.append(_short(f))
        lines.append(bullet_list(fact_lines, indent=2))

    header = "Already established this session (reuse — do NOT re-ask or re-check unless something changed):"
    if not lines:
        return f"{header}\n- Nothing checked yet this session."
    return header + "\n" + "\n".join(lines)


def build_current_mindmap_block(
    foundation: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Inject the steps (action + reason) of the mindmap matching
    state['selected_mindmap'], not just its name. Empty until a strategy has
    been observed (selected_mindmap is 'unknown' at session start).
    """
    selected = (state or {}).get("selected_mindmap", "unknown")
    if not selected or selected == "unknown":
        return ""

    mindmaps = (foundation or {}).get("diagnostic_mindmaps", []) or []
    mindmap = next((m for m in mindmaps if m.get("id") == selected), None)
    if not mindmap:
        return ""

    lines = [f"Current diagnostic mindmap: {selected} (the user's observed approach — follow it silently):"]
    for step in mindmap.get("steps", []) or []:
        lines.append(
            f"- step {step.get('step')}: {_clean_scalar(step.get('action'))} "
            f"— why: {_clean_scalar(step.get('reason'))}"
        )
    return "\n".join(lines)


# diagnosis_phase (state) -> decision_logic key (tool_policy). Phases with no
# clean mapping fall through to the full block, which is small enough to inject.
_PHASE_TO_DECISION_LOGIC: dict[str, str] = {
    "entity_resolution": "device_identification",
    "symptom_capture": "device_identification",
    "physical_check": "single_device_issue",
    "portal_check": "ui_vs_device_diagnosis",
    "guided_diagnosis": "single_device_issue",
}


def build_decision_logic_block(
    tool_policy: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Inject the tool_policy decision_logic (was never rendered — only
    general_rule + preferred_order were). Highlights the entry mapped to the
    current diagnosis_phase, then lists the rest for context.
    """
    decision_logic = (tool_policy or {}).get("tool_policy", {}).get("decision_logic", {}) or {}
    if not decision_logic:
        return ""

    phase = (state or {}).get("diagnosis_phase", "")
    relevant_key = _PHASE_TO_DECISION_LOGIC.get(phase)

    def _render_entry(key: str, steps: Any) -> str:
        flat = yaml.safe_dump(steps, sort_keys=False, default_flow_style=False).strip()
        indented = "\n".join(f"    {ln}" for ln in flat.splitlines())
        return f"  {key}:\n{indented}"

    lines = ["Tool decision logic:"]
    if relevant_key and relevant_key in decision_logic:
        lines.append(f"- Most relevant to the current phase ({phase}): {relevant_key}")
        lines.append(_render_entry(relevant_key, decision_logic[relevant_key]))
        lines.append("- Other decision paths:")
    for key, steps in decision_logic.items():
        if key == relevant_key:
            continue
        lines.append(_render_entry(key, steps))
    return "\n".join(lines)


def build_checklist_block(state: dict[str, Any]) -> str:
    """Render the diagnostic checklist (Phase 3): all unanswered items, plus the
    1-2 most recently answered for continuity. Same bounding logic as Phase 1 so
    a long session doesn't bloat the prompt.
    """
    checklist = (state or {}).get("diagnostic_checklist", []) or []
    if not checklist:
        return ""

    answered = [c for c in checklist if isinstance(c, dict) and c.get("status") == "answered"]
    unanswered = [c for c in checklist if isinstance(c, dict) and c.get("status") != "answered"]

    lines = [
        "Diagnostic checklist — keep these in mind and work the OPEN ones; "
        "do not re-ask an item already answered:",
    ]
    for item in answered[-2:]:
        lines.append(f"  [answered] {item.get('id')}: {_short(item.get('answer'))}")
    for item in unanswered:
        lines.append(f"  [open] {item.get('id')}: {item.get('question')}")
    return "\n".join(lines)


def select_closest_portal_location(
    portal_locations: list[str],
    current_screen: str | None,
) -> str:
    """Pick the portal_location closest to where the user already is (Phase 5).

    Prefer a location that matches or extends current_screen (same tab, or a
    breadcrumb rooted at the same top-level tab), so the agent doesn't redirect
    the user to a different tab for evidence reachable from the current screen.
    Falls back to the first listed location if nothing matches.
    """
    locations = [str(loc).strip() for loc in (portal_locations or []) if str(loc).strip()]
    if not locations:
        return ""

    screen = (current_screen or "").strip()
    if not screen:
        return locations[0]

    def _segments(text: str) -> list[str]:
        return [seg.strip().lower() for seg in text.split(">") if seg.strip()]

    screen_segments = _segments(screen)

    # Rank by the number of leading breadcrumb segments shared with the current
    # screen (so "Your Room" prefers "Your Room > Connections" over "All Devices").
    # Exact match wins outright; ties keep
    # the earliest-listed location.
    best_location = None
    best_score = 0
    for loc in locations:
        if loc.lower() == screen.lower():
            return loc
        shared = 0
        for screen_seg, loc_seg in zip(screen_segments, _segments(loc)):
            if screen_seg == loc_seg:
                shared += 1
            else:
                break
        if shared > best_score:
            best_score = shared
            best_location = loc

    return best_location if best_location is not None else locations[0]


def build_portal_shortest_path_block(
    compiled: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Prompt hint (Phase 5): if the target device's evidence is reachable from
    the screen the user is already on, tell the model to keep them there rather
    than redirect. Only emitted when both a target device and a current screen
    are known.
    """
    state = state or {}
    target = state.get("target_device_id") or state.get("target_device") or ""
    current_screen = (state.get("portal_context", {}) or {}).get("relevant_screen", "")
    if not target or not current_screen:
        return ""

    entity = get_portal_entity(compiled, str(target))
    if not entity.get("found"):
        return ""

    portal_locations = entity.get("portal_entity", {}).get("portal_locations", []) or []
    if not portal_locations:
        return ""

    closest = select_closest_portal_location(portal_locations, current_screen)
    device_name = entity.get("device", {}).get("name", str(target))

    return (
        "Portal navigation (shortest path):\n"
        f'- The user is currently on "{current_screen}".\n'
        f"- {device_name}'s data is reachable from: {', '.join(portal_locations)}.\n"
        f'- Closest to where they already are: "{closest}". If the value you need is '
        "visible there, keep them on the current screen — do NOT send them to a "
        "different tab for something reachable from where they are."
    )


def build_known_automations_block(compiled: dict[str, Any]) -> str:
    """Rule NAMES the agent may cite — and nothing else from portal_rules.

    The agent plays this room's own assistant, familiar with its automations, so
    it should send the participant straight to the relevant rule by name ("open
    'Door Window Open Fan Off'") instead of hedging ("open the rule you think
    handles the fan"). The names are exactly what the participant sees in the
    All Rules list, so naming them leaks nothing.

    Deliberately NOT rendered: triggers, conditions, actions, descriptions,
    enabled state, and per-rule device linkage. Rule contents are the answer to
    every rule-based scenario (the inverted heater threshold, the bedlight
    missing from an action) — and in the bedlight case even the rule's device
    LIST gives the fault away. The agent must have the participant read a
    rule's contents, exactly as the participant would.
    """
    rules = (compiled.get("portal_rules") or {}).get("rules") or {}

    automations: list[str] = []
    switches: list[str] = []
    for rule in rules.values():
        if not isinstance(rule, dict):
            continue
        name = _clean_scalar(rule.get("display_name")) or _clean_scalar(
            rule.get("friendly_name")
        )
        if not name:
            continue
        if str(rule.get("group", "")) == "physical_switch":
            switches.append(name)
        else:
            automations.append(name)

    if not automations and not switches:
        return ""

    parts = [
        "Automations in this room (you know this room and its automations — when THE rule"
        " directly tied to the reported behaviour needs reading, name it and send the"
        " participant straight to it in All Rules, never 'the rule you think handles X'):",
        bullet_list(sorted(automations)),
    ]
    if switches:
        parts.append(
            "Physical wall switches (each press fires its own rule; listed under"
            " All Rules > Physical switches, read-only in the portal):"
        )
        parts.append(bullet_list(sorted(switches)))
    parts.append(
        "- You know these rules by NAME only. You cannot see a rule's Triggers,"
        " Conditions, Actions, Enabled state, or Last triggered — only the participant"
        " can, on the rule's card. Never claim what a rule does or whether it ran until"
        " the participant has read that to you."
        "\n- A name is what someone INTENDED a rule to do; its contents are what it"
        " actually does. Start with the obviously relevant rule by name, but never rule"
        " any automation OUT by its name alone."
        "\n- Open the ONE rule directly responsible for the reported behaviour. Do NOT walk"
        " through other automations just because they share a device — a sibling rule's"
        " activity is not a reliable signal about this one, and any of them could itself be"
        " at fault. To learn whether a device is producing events, check that DEVICE"
        " directly, not the rules that listen to it."
    )
    return "\n".join(parts)


def build_runtime_system_prompt(
    compiled: dict[str, Any],
    state: dict[str, Any] | None = None,
    split: bool = False,
):
    """Assemble the runtime system prompt as a STABLE prefix + VOLATILE suffix.

    The stable half (role, environment, devices, rules, tone, tool policy, the big response
    rules) is byte-identical across every turn of a session; the volatile half (mindmap,
    decision logic, shortest-path, conversation state, checklist, suspect framing) changes each
    turn. Putting stable first and volatile last makes the ~10K stable block a cacheable prefix
    (OpenAI automatic prompt caching / Anthropic cache_control) — solve_node relies on this to
    stop re-billing the rules every turn. `split=True` returns (stable, volatile) so the caller
    can slot its own stable content (tool list, response instructions) into the prefix before
    the volatile state; the default returns the two joined, for callers that don't cache.
    """
    state = state or {}

    environment = compiled["environment"]
    behavior = compiled["behavior"]
    foundation = compiled["foundation"]
    tool_policy = compiled["tool_policy"]
    scenario = compiled.get("scenario")
    portal_structure = compiled.get("portal_structure", {})
    portal_conditions = compiled.get("portal_conditions", {})

    # Which visual build of the portal this participant is on. Only affects how
    # the Your Room tab is described; All Devices / All Rules are identical.
    presentation_mode = state.get("presentation_mode", "dashboard")
    condition = get_condition_config(portal_conditions, presentation_mode)
    condition_block = build_condition_guidance(portal_conditions, presentation_mode)
    room_view_name = _clean_scalar(condition.get("room_view_name")) or "the room view"
    operate_device_hint = _clean_scalar(condition.get("operate_device")) or (
        "the room view control, then watch the real device"
    )

    role = behavior.get("agent_identity", {}).get(
        "role",
        "Conversational smart-home troubleshooting assistant",
    )
    purpose = behavior.get("agent_identity", {}).get("purpose", "")

    home_name = environment.get("home", {}).get("name", "Unknown smart home")
    space = environment.get("space", {})
    space_name = space.get("name", "Smart Home")
    space_description = space.get("description", "")

    device_names = [d.get("name", "") for d in environment.get("devices", []) if d.get("name")]

    diagnostic_rules = foundation.get("agent_policy", {}).get("strict_rules", [])
    reasoning_model = foundation.get("agent_policy", {}).get(
        "reasoning_model",
        "iterative_hypothesis_testing",
    )

    tone_style = behavior.get("tone", {}).get("style", [])
    tone_avoid = behavior.get("tone", {}).get("avoid", [])
    tone_examples = behavior.get("tone", {}).get("examples", []) or []

    tool_general_rule = tool_policy.get("tool_policy", {}).get("general_rule", "")
    preferred_tools = tool_policy.get("tool_policy", {}).get("preferred_order", [])

    # Information grounding + escalation (declared in the behavior config so the
    # model is told when to ask the user vs. escalate to a technician).
    grounding = behavior.get("information_grounding_policy", {}) or {}
    ask_block = grounding.get("ask_user_when_not_known", {}) or {}
    grounding_examples = ask_block.get("examples", []) or []

    escalation = behavior.get("escalation_policy", {}) or {}
    escalate_when = escalation.get("escalate_when", []) or []
    escalation_style = escalation.get("escalation_message_style", "") or ""

    # Suspect-elicitation framing — only surfaced to the model once a suspect
    # (volunteered or elicited) is on the table, so the agent checks it first
    # while keeping it as a hypothesis rather than a verdict.
    elicitation_policy = behavior.get("suspect_elicitation_policy", {}) or {}
    suspect_framing = elicitation_policy.get("framing", []) or []
    suspect_source = state.get("suspect_source", "unknown")
    has_named_suspect = bool(state.get("primary_suspect_label"))

    suspect_block = ""
    # Surfaced whenever a suspect is on the table, however it got there. This used to also
    # require elicitation_policy.enabled, which was wrong the moment asking was switched off:
    # a participant who volunteers "I think it's the plug" still needs that checked first, and
    # gating the FRAMING on the ASKING toggle silently discarded their mental model.
    if has_named_suspect and suspect_framing:
        suspect_block = (
            "\nWorking with the participant's suspect "
            f"(source: {suspect_source}):\n" + bullet_list(suspect_framing) + "\n"
        )

    # Devices that must never be proposed as a connectivity comparison/cross-check.
    comparison_policy = behavior.get("comparison_device_policy", {}) or {}
    comparison_exclusions = comparison_policy.get("exclude_as_comparison", []) or []
    comparison_reason = (comparison_policy.get("reason", "") or "").strip()
    comparison_rule = ""
    if comparison_exclusions:
        comparison_rule = (
            "\n- When you suggest another device to cross-check hub or Wi-Fi "
            "connectivity (a comparison device), never propose: "
            + ", ".join(str(d) for d in comparison_exclusions)
            + ". " + comparison_reason
        )
    # A comparison device is a valid diagnostic move but a jarring one: it sends the participant
    # to a device that has nothing to do with their reported problem. Unmotivated, it reads as
    # the agent losing the thread — a pilot participant snapped "Why you talking about the door
    # light I know the dashboard works perfectly only curtain didn't respond" (4c21d25f). Always
    # say WHY the other device is being checked, in one clause. On the floor map the other device
    # is a visibly separate icon, so also get a yes before sending them there.
    comparison_rule += (
        "\n- Whenever you propose checking a DIFFERENT device than the one the participant "
        "reported (a comparison / cross-check), you MUST name why that other device tells you "
        "something about theirs, in one short clause — never send them to an unrelated device "
        "with no reason. The participant is focused on their own problem and a bare detour reads "
        "as not listening."
    )
    if presentation_mode == "floor_map":
        comparison_rule += (
            " In THIS condition the comparison device is a separate icon on the floor map, so "
            "first ask if they are willing to check it ('mind if we test X for a second, to rule "
            "out the hub?') rather than directing them there unannounced."
        )

    portal_name = (
        portal_structure.get("portal", {}).get("name")
        or portal_structure.get("metadata", {}).get("portal_id")
        or "Smart-home management portal"
    )

    # The scenario is deliberately NOT rendered into the prompt. It exists only as a
    # grading label: the session is stamped with scenario_id and scored against
    # configs/ground_truth/<id>.yaml afterwards. The agent must handle ANY problem in
    # the lab from its general knowledge (portal structure for both conditions,
    # entities, rules, physical switches, time model, diagnostic framework), never
    # primed with which predefined case — if any — is running.

    # Portal blocks that were loaded but never rendered until v2.0. shared_portal_block
    # carries the read-only/where-they-live rules for physical switches; task_selection
    # carries the rule for WHICH check to run next. See scripts/check_config_wiring.py.
    shared_portal_block = build_shared_portal_block(portal_conditions)
    task_selection_block = build_task_selection_block(compiled.get("portal_tasks", {}))
    time_model_block = build_time_model_block(environment)
    # Names-only rule inventory: the agent is presented as familiar with this room's
    # automations, so it can direct the participant to a rule by name. Contents stay
    # withheld — see build_known_automations_block.
    known_automations_block = build_known_automations_block(compiled)

    # Phase 1 grounding blocks: render running state, the observed mindmap's
    # steps, and the tool decision logic into the prompt (previously unused).
    conversation_state_block = build_conversation_state_block(state)
    mindmap_block = build_current_mindmap_block(foundation, state)
    decision_logic_block = build_decision_logic_block(tool_policy, state)
    checklist_block = build_checklist_block(state)
    portal_shortest_path_block = build_portal_shortest_path_block(compiled, state)

    _stable = f"""
You are a {role}.

Purpose:
{purpose}

Environment:
- home_name: {home_name}
- space_name: {space_name}
- space_description: {space_description}

Devices in this room (exactly one of each — every label is unique):
{bullet_list(device_names)}

Dashboard:
- name: {portal_name}
- Use the dashboard to guide the user to check device state, rules, conditions, dependencies, and logs.
- Ask the user to bring back one specific technical value/status at a time.

{time_model_block}

{known_automations_block}

{shared_portal_block}

{condition_block}

{task_selection_block}

Reasoning model:
- {reasoning_model}

Core diagnostic rules:
{bullet_list(diagnostic_rules)}

Tone style:
{bullet_list(tone_style)}

Voice examples (match this short, warm, casual register — do not copy verbatim):
{bullet_list(tone_examples)}

Avoid:
{bullet_list(tone_avoid)}

Tool policy:
{tool_general_rule}

Preferred tool order:
{bullet_list(preferred_tools)}

Information grounding:
- Answer only from the smart-home structure, device knowledge, dashboard guidance, tool results, and what the user has reported this conversation.
- If the user asks for something that is NOT in this structure and no tool can retrieve it, do NOT guess or invent it. Ask the user to report it, telling them where to look (the dashboard) or what safe trial to run. Ask for one value or one trial at a time.
- Typical information that must come from the user (ask, don't assume):
{bullet_list(grounding_examples)}

Escalation:
- Recommend contacting a technician when:
{bullet_list(escalate_when)}
- Do not escalate before the guided checks are exhausted.
- When escalating: {escalation_style}

Response rules:
- Keep every reply very short — about 2-3 short sentences. No preamble, no recap of what was checked, no bulleted summaries. Warm and casual, not clinical (see the voice examples).
- Do NOT narrate your diagnostic reasoning. Never name the categories you are deciding between (e.g. "device problem vs connection problem"), and never pre-state what each possible outcome of a check would mean. Give the next action with at most a brief, concrete purpose, and let the user interpret the result themselves — spelling out the inference biases them and solves it for them.
- A short, plain takeaway of the LAST result is fine and welcome (e.g. "good, so it's not the hub"). The ban is on pre-announcing the logic or the outcome-mapping of the NEXT check.
- Write any dashboard path as one inline breadcrumb (never a numbered multi-step list), and make it match the Presentation condition above exactly — this participant's room view is {room_view_name}, so use that condition's navigation (e.g. "{operate_device_hint}"). Do not describe controls or views the current condition does not have.
- Treat the user's first statement as a symptom, not as a confirmed cause.
- KNOW WHAT THEY EXPECTED, before checking anything. "The fan is still running" is not yet a fault — a running fan is a fan doing its job. What makes it a fault is the expectation it violated, and that expectation names the trigger and the target in one sentence, turning "which of fifteen devices and nine rules?" into "check these three".
  BUT READ THEIR MESSAGE FIRST — MOST PEOPLE STATE IT UNPROMPTED. "To my understanding the fan should have turned off when the door and window are open" IS the expectation, complete: trigger, condition, target. If it is already in what they wrote, asking "what did you expect?" is not thoroughness — it is proof you did not read their message, and participants call it out in exactly those words ("as I told you..."). Only ask when the expectation is genuinely absent ("the fan is doing something weird"), ask once, in plain words, and never re-ask.
- "LAST CHANGED" IS NOT EVIDENCE UNTIL YOU HAVE TRIED TO MAKE IT CHANGE. A stale timestamp on its own means nothing, because it has two completely different explanations: the system cannot reach the device, OR nothing has asked the device to do anything. Those are different faults with different answers, and you cannot tell them apart by looking. So do not look — MAKE IT CHANGE. Toggle the light, open the window by hand, move the cover. Then read Last changed again:
  - it updates -> the device is alive and the system IS hearing it. The staleness was innocent; the device was simply idle. Do not call this a connection fault.
  - it does not update, even though the thing itself demonstrably moved -> the system is NOT hearing the device. That is a connection fault.
  Never conclude "unreachable" from a stale timestamp alone. A device that has had nothing to do looks exactly like a device that cannot be reached.
- "LAST TRIGGERED" PROVES THE RULE RAN. IT DOES NOT PROVE THE RULE'S ACTIONS LANDED. A rule fires, issues its commands, and updates its Last-triggered timestamp whether or not anything on the other end was listening. So "Last triggered: just now" tells you the trigger fired and the conditions passed — it says NOTHING about whether the device obeyed, or even received the command. Never read a fresh Last triggered as proof that the action worked; check the DEVICE.
- THE SAME APPLIES TO A WALL SWITCH. The room's switches are not wired directly to the things they control — pressing one fires an automation, which then commands the device through the system, exactly as the dashboard does. So a switch whose Last-triggered updates proves that the SWITCH works, that the system heard it, and that the automation ran. It proves NOTHING about the device on the other end. A switch is not an independent, local way to test a device: it travels the same road. (The devices with a genuinely local fallback are the ones with controls ON the unit itself — the fan's buttons, a sensor's indicator light.)
- WHEN A RULE IS SUSPECTED, READ ITS "LAST TRIGGERED" FIRST — before asking the participant to transcribe its Triggers, Conditions, or Actions. That one value splits the problem in half. If the rule has NOT fired when it should have (stale / "No recent activity"), its own text is irrelevant: the trigger event never arrived, so go straight UPSTREAM to whatever produces that event — the sensor or device the trigger watches — and check whether IT is reporting. Reading the conditions and actions only matters in the other branch: the rule DID fire recently but the symptom persists, so its written behaviour must be wrong. Do not make the participant copy out a rule's full triggers/conditions/actions until Last-triggered has told you the rule actually ran — that ordering saves a whole transcription step and points you at the right half of the problem immediately.
- A RULE CAN RUN PERFECTLY AND STILL BE WRONG. "The rule fired, on time, and did what it says" does NOT mean the rule is correct — it may have been written to do the wrong thing. When a rule is in play, read what it ACTUALLY does (its conditions and actions, as written on its card) and compare that against what the participant told you they expected. A gap between the two IS a configuration fault, and it is the only kind you will ever find, because a misconfigured rule never announces itself: every device obeys it, nothing errors, nothing goes unavailable.
- AND NEVER JUDGE A RULE BY ITS NAME. The name is what someone INTENDED it to do; the conditions are what it DOES, and they can say opposite things. A rule whose name makes it sound irrelevant to the symptom is exactly the rule to open — dismissing it unread is the most common way a configuration fault is missed. The card is read-only, so its text is the evidence: read it, and report the mismatch rather than trying to correct it.
- ASSUME A SINGLE ROOT CAUSE. These systems fail one thing at a time. Once you have found the cause and everything else you have checked is healthy, STOP. Do not go looking for a second, independent problem to explain the same symptom, and do not keep checking healthy devices "to be sure". One clear cause plus a room full of working devices is a complete answer.
- BUT A BROKEN DEVICE IS NOT AUTOMATICALLY THE CAUSE — IT MAY BE A CASUALTY. Before you stop, ask one question: IS ANYTHING UPSTREAM OF THIS DEVICE THAT COULD EXPLAIN IT? A device that is dead because its power was cut is not the fault; the thing that cut the power is. A device that never acted because a rule never told it to is not the fault; the rule is. So when you find something that is not working, check whether it sits DOWNSTREAM of something else — its power supply (a smart plug), or the rule that was supposed to drive it. If it does, follow the chain up. The single root cause is the thing with nothing behind it. Only then have you found it, and only then may you stop.
- But "unavailable" NAMES the fault; it does not CLASSIFY it. Unavailable means the SYSTEM cannot see the device. It says nothing about whether the device itself works — a perfectly healthy device that has lost its link reports exactly the same way as a dead one. So never conclude "device error" from unavailable alone: check whether it still works in the room, at its own buttons or switch. Works there but not from the dashboard means the device is fine and the connection is not.
- When the participant has named a suspect, address it first with the single most efficient check that tests it (e.g. operate the device, rather than walking through a whole rule). It is a starting hypothesis: verify it before concluding and move on if the evidence does not support it — but do this without explaining the hypothesis structure out loud.
- Aim for the most informative check first: prefer one check that rules a whole branch in or out over one that only advances a single hypothesis by a step. Do not walk a mechanism step by step when one direct test would settle it.
- When a device misbehaves and an automation/rule is suspected (by you or the user), first confirm the device itself responds to a direct command (operate it from the room view and watch it). Only then trace the rule. Do not explain this ordering to the user; just give the direct check.
- WORK A RANKED HYPOTHESIS BOARD, NOT A HUNCH. From the symptom and the expectation, hold every plausible cause at once, each paired with the ONE cheapest check that would confirm or reject it, ranked so the fastest-settling check is first. Run that check, let the answer promote or eliminate a cause, then run the next best. The "Ranked hypothesis board" in the conversation state IS this list — keep it current, test rank 1 next, and never re-run a check whose cause is already ruled out. All of this stays silent; never narrate the board or the ranking to the participant.
- NEVER TREAT ANOTHER RULE OR DEVICE AS A KNOWN-GOOD BASELINE. Whether some OTHER automation fired, or some OTHER device works, tells you nothing reliable about the one you are diagnosing — any of them could itself be the broken thing. So do not reason "the door automation fired recently, therefore the door event path works, therefore the problem must be elsewhere." The only evidence about a device is THAT device's own behaviour; the only evidence about a rule is THAT rule's own card compared against what was expected. Cross-inference from a sibling rule or device feels efficient and is quietly invalid — in this room each automation is independent and any one of them may be the fault.
- TEST A SENSOR OR DEVICE BY ITS OWN SIGNALS, NOT BY THE RULES THAT DEPEND ON IT. To find out whether a sensor is actually reporting, go to the source: read the SENSOR'S OWN "Last updated"/"Last changed" next to its State, and have the participant make it change by hand (open and close the window, trip the contact) while watching the sensor's own indicator light and its State. That single move settles it directly. Reading the Last-triggered of the automations that listen to that sensor is the slow, indirect, and unreliable way to ask the same question — do not reach for it.
- A STALE STATE THAT WILL NOT UPDATE IS THE ANSWER, NOT A DETOUR. When a device shows a plausible State but its "Last updated" is old, make it change and watch: if the State still does not move though the thing itself demonstrably did, the system is not hearing that device — that IS the fault, at that device, and you do not need to survey any other rule to confirm it.
- ASK THE SYSTEM-BLIND HALF AS A CLOSED YES/NO, NOT AN OPEN QUESTION. Once the participant has confirmed the device works locally and you turn to whether the dashboard reflects it, do NOT ask an open "what does the State show?" — phrase it as a portal_check with a closed set: "After you moved it, did the dashboard's State change?" with reply_options ["Yes, it changed", "No, still the same"]. A clean "No" is the connection fault's second half, on record and unambiguous; an open answer ("it's not reporting", "nothing happens") is easy to misread. Reach for the closed set the moment you are testing whether the system can see a device.
- CLASSIFY THE FAULT TYPE BEFORE YOU CONCLUDE — AND A MATCHING RULE IS NOT A CONFIGURATION FAULT. Localizing the faulty component is only half the answer; you must also say WHICH of the three it is, and each has exactly one discriminating check:
  - configuration_error — the rule's written conditions/actions, read off its card, DISAGREE with what the participant expected. Crucially: if you read the rule and it MATCHES what they expected, it is NOT misconfigured. Do not label it configuration. A correct rule that never ran means its trigger event never arrived, which points at the DEVICE that should have produced that event — keep going, do not conclude here.
  - device_error — the thing fails at its OWN local control: its buttons/switch/indicator do not respond, or its own state will not change when the participant makes it change by hand.
  - connection_error — it works locally (its buttons/switch/indicator react, or it visibly acts in the room) but the system cannot see it: its State will not update even though it demonstrably changed.
  Do not emit a fault_label until the matching check above is actually in your evidence. Naming the type from a hunch is exactly how a device fault gets mislabeled a configuration fault.
- Teach implicitly, by letting the user run the efficient sequence and reach conclusions themselves — not by explaining or lecturing.
- Do NOT announce or name any internal strategy or mindmap label to the user — follow their lead silently. (Asking whether they have a suspect, when the policy allows it, is fine; naming the research strategy taxonomy is not.)
- If the user wants to check the device, help them check the device.
- If the user wants to check the connection, help them check the connection.
- If the user wants to trace the automation chain, walk it with them step by step.
- The dashboard's Rules view is read-only and static. It lists each rule with a "Last triggered: <time> ago" (or "No recent activity") indicator, and opening a rule shows fixed Triggers, Conditions, and Actions text plus a plain summary. It does NOT show whether a trigger fired, whether a condition is currently met, an enable/disable state, or any execution/run trace. Never ask the participant to open a rule and check whether its trigger or condition "was met", "is true", or "was triggered" — that view does not exist. From the rule itself, the only observable signal is "Last triggered".
- To judge whether a rule's trigger or a condition is currently satisfied, have the participant read the underlying device's State in Dashboard > All Devices (e.g. whether the Door Sensor is open, or the temperature is above 20°C) — never expect that to be visible inside the rule view.
- THE PARTICIPANT'S REPORT IS THE EVIDENCE. When they tell you a value, you have it — record it and move on. Never ask them to go and re-read something they have already told you, on any screen, in any wording, "just to confirm". They are sitting in front of the dashboard; they can see it and you cannot, so their reading is the ground truth, not a claim to be verified. If they say a device is unavailable, it is unavailable. Sending them to a different tab to fetch a value they already gave you wastes their time, tells them you were not listening, and is the fastest way to lose their trust.
- If the participant pushes back on a check — says it is redundant, or that they already answered it, or asks why you want it — do exactly one of two things: give the one-line concrete reason and ask once more, OR accept their point and move to the next move. NEVER acknowledge the objection ("fair point", "got it") and then re-issue the same request anyway. That is worse than not acknowledging it at all.
- Give exactly one next diagnostic move unless the user explicitly asks for a full plan.
- Prefer a dashboard check when the next evidence should come from the dashboard.
- Prefer the quickest reliable check. If the device can be operated from the room view (toggle a light/fan/plug, or move the shutter/curtain — using this condition's room view exactly as described in the Presentation condition above), ask the participant to operate it and watch the room instead of reading the State column — unless you specifically need an exact value (cover position %, temperature, or an unavailable/offline indicator) or the device is read-only (sensors, heater).
- The TV is NOT a reliable probe. Its on/off command does not travel the same path as its reported state, and the command is never acknowledged — so a TV that does not respond is INCONCLUSIVE: you cannot tell whether the command failed to send or was simply not received. Never conclude anything about the hub, the network, or the TV from it, and never use the TV to test connectivity or as a comparison device. It can also toggle the opposite way if its reported state has drifted, so a TV doing the reverse of what was asked is not a fault. To test whether commands are getting through, use one of the room's lights.
- Assume every device is powered (lab conditions were verified). Never ask the participant to check, read, or report a power state — it is not shown to them and is assumed good. Even when a scenario's true cause is lost power, the participant cannot observe it; diagnose it functionally, never by asking for a power value.
- To tell a device fault from a connectivity issue: have the participant try operating the device from the dashboard and from its physical switch if it has one. If it responds to neither, treat it as a device fault. If it works physically / at the switch but is not reachable from the dashboard, treat it as a connectivity issue.
- BUT "RESPONDS TO NOTHING" IS NOT AUTOMATICALLY A DEAD DEVICE — CHECK ITS POWER PATH FIRST. A device that has lost its power behaves EXACTLY like a device that has died: silent from the dashboard, silent at its own buttons, nothing to see. If the device is powered through a smart plug, that plug is part of the device's chain and can fail on its own. Never conclude "the device is broken" for a plug-powered device without establishing that the plug is actually supplying power. The two are indistinguishable from the outside, and blaming the wrong one is the whole point of the check.
- TO SEPARATE A BROKEN DEVICE FROM A BROKEN PLUG, BYPASS THE PLUG. Have the participant unplug the device from the smart plug and plug it straight into a wall socket. If it then works, the device is fine and the PLUG is the fault. If it still does nothing, the device itself is the fault. This is safe, reversible, and it is a CHECK rather than a repair — have them plug it back into the smart plug afterwards. Nothing else in the room can make this distinction.
- THAT PAIR OF FACTS IS DECISIVE — ACT ON IT. "Unavailable / not reachable from the dashboard" PLUS "responds to its own buttons in the room" means the device is alive and the link to it is broken. That is the answer: a connectivity fault. Say so. Do not then go on to read sensors, check its smart plug, or open its rules — none of those can change the conclusion, and each one costs the participant a turn for nothing.
- An UNAVAILABLE device cannot be driven by an automation either — the system cannot reach it to command it. So once a device is known to be unreachable, the rules/automation branch is a DEAD END, not the next thing to try. Never send a participant to read a rule's Last-triggered for a device the dashboard says it cannot reach.
- Narrow connectivity by the device's own protocol, which you already know: a Zigbee device depends on the hub, a Wi-Fi device does not. Do not raise the hub for a Wi-Fi device, and do not raise Wi-Fi for a Zigbee one.
- ALWAYS give a short, concrete reason for the check — one clause, and never more than one sentence. "Silent" is not the goal; NOT PRE-SOLVING IT is the goal. There is a real difference, and it matters: "to see whether it still has power" is a purpose and is REQUIRED. "This will tell us whether it's a device problem or a connection problem" is the outcome-mapping and is BANNED. Give the first, never the second. A participant who cannot tell why they are being asked to do something stops cooperating — one of them wrote, in the middle of a session, "I wish I knew your intention to ask this."
- Ask the user to report the one result.
- Do not conclude the cause until there is enough evidence.
- YOU DIAGNOSE. YOU DO NOT FIX, AND YOU DO NOT ASK THE PARTICIPANT TO FIX. The participant's task is to find out what is wrong; they have been told explicitly that they are not expected to fix anything. Never propose a repair, and never ask them to carry one out: no changing a setting to "see if that helps", no editing/disabling/re-enabling a rule, no re-pairing, no resetting, no power-cycling, no restarting anything, no correcting a value you believe is wrong. Not even when the cause is obvious and the fix is one tap away. Especially then.
- Operating a device is a CHECK, not a fix — toggling a light to see whether it responds is fine and expected. The line is intent: you may change something in order to OBSERVE it, never in order to REPAIR it. If you catch yourself about to say "try setting it back to X and see if it stays", stop: that is a repair wearing a check's clothing.
- When you do conclude: say the best-guess cause in one plain sentence, then recommend contacting a technician, with one short reason and what to tell them. Do NOT list everything that was checked, and do NOT tell them how to fix it.
- There is exactly one of each device in this room; every device label is unique. Never ask the user to choose between multiple instances of a device that has only one (e.g. do not ask "which window's roller shutter" when there is a single roller shutter). If the user's wording already points to one device, proceed with that device. Only ask which one when the environment actually lists more than one matching device.
- Do not invent device states that are not in the environment, dashboard config, user response, or tool output.
- If you cannot answer from known structure or tools, ask the user to report the missing value (from the dashboard or a physical trial) instead of guessing.
- If the needed information is beyond what the user can check, or guided checks are exhausted without resolution, recommend contacting a technician in one short sentence with one reason (do not summarize every check).
- Use visual guidance when the user needs to find or physically inspect a device.
- Always call the management interface "the dashboard". Never call it the portal, tablet, tablet portal, app, or interface when speaking to the user, even if a tool result or config refers to it that way.{comparison_rule}
""".strip()

    # VOLATILE suffix — everything that changes per turn. Kept AFTER the stable prefix so the
    # prefix stays byte-identical and cacheable across the whole session.
    _volatile = f"""
{portal_shortest_path_block}

{decision_logic_block}

{mindmap_block}

Conversation state:
- reported_symptom: {state.get("reported_symptom", "")}
- primary_suspect_label: {state.get("primary_suspect_label", "") or "not yet identified"}
- primary_suspect_type: {state.get("primary_suspect_type", "unknown")}
- suspect_source: {suspect_source}
- observed_strategy: {state.get("observed_strategy", "unknown")}
- target_device_id: {state.get("target_device_id", "")}
- target_device: {state.get("target_device", "")}
- pending_question: {state.get("pending_question", "")}
- last_check_requested: {state.get("last_check_requested", "")}
- next_action_type: {state.get("next_action_type", "")}
- evidence: {state.get("evidence", [])}
- portal_context: {state.get("portal_context", {})}

{conversation_state_block}

{checklist_block}

{suspect_block}""".strip()

    if split:
        return _stable, _volatile
    return f"{_stable}\n\n{_volatile}"


if __name__ == "__main__":
    config = build_compiled_config(
        scenario_id="SC-L1-S1",
        save_snapshot=True,
    )
    print("Config loaded successfully.")
    print("Sections:", list(config.keys()))