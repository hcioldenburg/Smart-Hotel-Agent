"""Which config keys actually reach the model — and which are just sitting there.

A YAML key that nothing reads is not neutral. It is a file that silently absorbs your
edits: you change a policy, the agent's behaviour does not move, and there is nothing to
tell you why. Every structural problem found in this repo so far has that shape.

  * guided_diagnostic_behavior.yaml carried a worked example built on a real study
    scenario. It never shipped, because the key was dead — but "dead" was invisible, and
    the moment anyone wired that key in, the answer would have gone straight to the model.
  * foundation's strict_rules told the agent to explain its reasoning while the response
    rules told it not to. Both live, contradicting each other, in every prompt.
  * portal_rules.yaml — 15 rules, verified against Home Assistant — is read by nothing.

HOW IT WORKS. Not by grepping for key names, and not by looking for the config's text in
the prompt — both lie. Grep misses a key read through a variable; text-matching cannot
tell "this key reached the model" from "some other key happens to contain the same
sentence", which is precisely the case for portal_rules (its rule descriptions echo text
that portal_entities also carries).

Instead: wrap the compiled config in dicts that RECORD EVERY KEY ACCESS, then run the real
prompt builder and invoke every real tool, and see which keys nobody ever touched. That is
ground truth about wiring, and it cannot be fooled.

Conditional blocks (the mindmap, the suspect framing, the decision logic) only render when
the session state activates them, so the probe drives the prompt several times with states
that switch each one on. Otherwise a live-but-conditional key looks dead.

    python scripts/check_config_wiring.py            # report
    python scripts/check_config_wiring.py --strict   # exit 1 if any key is inert

DEAD is not automatically a bug. Some keys are unread ON PURPOSE — portal_rules.yaml is the
source of truth for scripts/check_rule_drift.py, and its rule CONTENTS must not reach the
prompt: they describe what every automation does, which is the answer to any rule-based
scenario. (Since 2026-07-17 the rule NAMES alone are rendered — the agent is presented as
familiar with the room's automations — via build_known_automations_block; everything else
in a rule stays withheld.) Declare intentionally-unread keys in INTENTIONALLY_UNREAD, with
a reason. If you cannot write the reason, the key is not intentionally unread — it is just
dead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from smart_home_agent_backend.utils import config_loader  # noqa: E402
from smart_home_agent_backend.utils import tools as agent_tools  # noqa: E402

PRESENTATION_MODES = ["dashboard", "floor_map"]

# Keys that are unread ON PURPOSE. Each needs a reason.
INTENTIONALLY_UNREAD: dict[str, str] = {
    "portal_rules.rules_tab": (
        "Source of truth for check_rule_drift.py. Must NEVER reach the prompt: it "
        "describes what each automation does, which IS the answer to any rule-based "
        "scenario. check_prompt_leakage.py flags it as a latent leak for exactly this "
        "reason. See configs/ground_truth/README.md."
    ),
    # portal_rules.rules is now PARTIALLY live: build_known_automations_block reads each
    # rule's display_name/group so the agent can name rules outright (it plays the room's
    # own assistant). Contents (triggers/conditions/actions/descriptions/device linkage)
    # are still never rendered — see that function's docstring. The tracer will report
    # the key as live; that is expected.
    "portal_rules.device_rule_counts_derived": "Derived data for the UI and drift checker.",
    "portal_rules.automation_mapper_support": "Bootstrap tooling metadata, not agent-facing.",
    "portal_entities.home_assistant": "HA connection metadata for the bootstrap scripts.",
    "portal_entities.physical_switches": (
        "Consumed by the participant-facing UI. The agent is told about switches through "
        "portal_tasks.tasks.check_switch_last_pressed instead."
    ),
}


# ── Access tracing ────────────────────────────────────────────────────────────────

ACCESSED: set[str] = set()
TOOL_OUTPUTS: list[str] = []


def dumped_wholesale(section: str, key: str) -> bool:
    """True if a tool hands the model an entire config section, sub-keys and all.

    get_portal_overview does exactly this: `config.get("portal_structure")` and return the
    whole object. Only ONE key access is traced — the section itself — yet every sub-key
    underneath lands in the model's context. Tracing alone would call them all dead, which
    is the opposite of the truth and would invite someone to delete live config.
    """
    needle_section = f'"{section}"'
    needle_key = f'"{key}"'
    return any(
        needle_section in out and needle_key in out
        for out in TOOL_OUTPUTS
    )


class TrackedDict(dict):
    """A dict that remembers which keys were read, and where it sits in the config."""

    def __init__(self, data: dict, path: str = "") -> None:
        super().__init__(data)
        self._path = path

    def _record(self, key: Any) -> None:
        ACCESSED.add(f"{self._path}.{key}" if self._path else str(key))

    def __getitem__(self, key: Any) -> Any:
        self._record(key)
        return super().__getitem__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        self._record(key)
        return super().get(key, default)

    def __contains__(self, key: Any) -> bool:
        self._record(key)
        return super().__contains__(key)


# build_decision_logic_block yaml.safe_dump()s a slice of the config straight into the
# prompt. SafeDumper refuses anything that is not exactly a dict, so teach it that a
# TrackedDict is one — otherwise tracing changes behaviour, which would defeat the point.
yaml.SafeDumper.add_representer(
    TrackedDict,
    lambda dumper, data: dumper.represent_dict(dict(data)),
)


def wrap(node: Any, path: str = "") -> Any:
    """Recursively wrap dicts so key reads are recorded with their full path."""
    if isinstance(node, dict):
        wrapped = {
            k: wrap(v, f"{path}.{k}" if path else str(k))
            for k, v in node.items()
        }
        return TrackedDict(wrapped, path)
    if isinstance(node, list):
        return [wrap(v, path) for v in node]
    return node


# ── Exercising every code path that can read config ───────────────────────────────

def exercise(compiled_factory) -> None:
    """Run everything that reads config: the prompt (all conditions, all conditional
    blocks) and every tool."""
    base = compiled_factory()

    # States chosen to switch ON each conditionally-rendered prompt block. Without these,
    # a key that is live-but-conditional (the mindmap steps, the suspect framing, the
    # phase-specific decision logic) is indistinguishable from a dead one.
    states: list[dict[str, Any]] = [
        {"presentation_mode": mode} for mode in PRESENTATION_MODES
    ] + [
        {
            "presentation_mode": "floor_map",
            "selected_mindmap": "dependency_chain_diagnosis",   # foundation.diagnostic_mindmaps
            "primary_suspect_label": "Smart Fan",               # behavior.suspect_elicitation_policy.framing
            "suspect_source": "volunteered",
            "diagnosis_phase": "portal_check",                  # tool_policy.decision_logic
            "target_device_id": "smartfan",                     # portal_entities portal_locations
            "portal_context": {"relevant_screen": "Your Room"},
            "diagnostic_checklist": [{"id": "c1", "question": "q", "status": "open"}],
            "checked_devices": ["smartfan"],
            "confirmed_facts": [{"content": "fan responds", "source": "user"}],
        },
    ]

    for state in states:
        config_loader.build_runtime_system_prompt(base, state=state)

    device_ids = [d["id"] for d in base["environment"].get("devices", [])]
    task_names = list((base.get("portal_tasks") or {}).get("tasks", {}))

    def _run(tool, arg: dict[str, Any] | None = None) -> None:
        try:
            TOOL_OUTPUTS.append(str(tool.invoke(arg or {})))
        except Exception:
            pass

    _run(agent_tools.get_space_info)
    _run(agent_tools.list_devices)
    _run(agent_tools.check_hub_status)
    _run(agent_tools.get_portal_overview)
    _run(agent_tools.list_portal_tasks)

    for did in device_ids:
        for tool in (
            agent_tools.get_device_info,
            agent_tools.get_device_knowledge,
            agent_tools.get_portal_entity_info,
            agent_tools.get_device_portal_check_guide,
            agent_tools.check_device_connectivity,
            agent_tools.show_device_location,
        ):
            _run(tool, {"device_name_or_id": did})

    for task in task_names:
        _run(agent_tools.get_portal_task_guide, {"task_name": task})

    for hub in base["environment"].get("hubs", []):
        _run(agent_tools.show_hub_location, {"hub_name_or_id": hub["id"]})


def main() -> int:
    parser = argparse.ArgumentParser(description="Report which config keys reach the model.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any key is inert.")
    args = parser.parse_args()

    real_build = config_loader.build_compiled_config
    plain = real_build(save_snapshot=False)

    def tracked_build(*a: Any, **kw: Any) -> Any:
        return wrap(real_build(*a, **{**kw, "save_snapshot": False}))

    # Tools import build_compiled_config by name, and cache it, so patch both bindings
    # and clear the cache — otherwise the tools quietly use the untracked original.
    config_loader.build_compiled_config = tracked_build       # type: ignore[assignment]
    agent_tools.build_compiled_config = tracked_build         # type: ignore[assignment]
    if hasattr(agent_tools._get_config, "cache_clear"):
        agent_tools._get_config.cache_clear()

    try:
        exercise(tracked_build)
    finally:
        config_loader.build_compiled_config = real_build      # type: ignore[assignment]
        agent_tools.build_compiled_config = real_build        # type: ignore[assignment]
        if hasattr(agent_tools._get_config, "cache_clear"):
            agent_tools._get_config.cache_clear()

    print("\n══ Config wiring ═════════════════════════════════════════════")
    print("   Traced every key read while building the prompt (both conditions,")
    print(f"   conditional blocks forced on) and invoking every tool.")
    print(f"   {len(ACCESSED):,} distinct key paths were touched.\n")

    sections = [
        "environment", "behavior", "foundation", "knowledge", "tool_policy",
        "portal_structure", "portal_entities", "portal_tasks", "portal_rules",
        "portal_conditions",
    ]

    dead: list[str] = []
    intentional: list[str] = []

    for section in sections:
        data = plain.get(section)
        if not isinstance(data, dict):
            continue

        print(f"── {section}")
        for key in data:
            if key == "metadata":
                continue
            path = f"{section}.{key}"

            if path in ACCESSED:
                print(f"     live    {key}")
            elif dumped_wholesale(section, key):
                print(f"     live    {key}  (whole section dumped by a tool)")
            elif path in INTENTIONALLY_UNREAD:
                print(f"     unread  {key}  (on purpose)")
                intentional.append(path)
            else:
                print(f"     DEAD    {key}")
                dead.append(path)
        print()

    print("══ Result ════════════════════════════════════════════════════\n")

    if intentional:
        print(f"  {len(intentional)} key(s) deliberately kept away from the agent:\n")
        for path in intentional:
            print(f"    - {path}\n        {INTENTIONALLY_UNREAD[path]}\n")

    if dead:
        print(f"  {len(dead)} key(s) loaded but never read by anything:\n")
        for path in dead:
            print(f"    - {path}")
        print(
            "\n  Each is one of three things:\n"
            "    documentation   -> fine, but say so in a comment, so nobody edits it\n"
            "                       expecting the agent's behaviour to change\n"
            "    should be wired -> connect it, then re-run check_prompt_leakage.py\n"
            "    obsolete        -> delete it\n"
            "\n  What it must not be is undeclared. A key that looks live and is not will\n"
            "  absorb your edits in silence.\n"
        )
    else:
        print("  Every key either reaches the model or is declared intentionally unread.\n")

    return 1 if (dead and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
