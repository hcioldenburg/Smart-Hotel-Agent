# Ground truth — the answers

One file per study scenario. Each records **what is actually broken in the lab**,
why it produces the symptom, and which checks genuinely discriminate.

## The one hard rule

**Nothing in this directory may ever reach the agent's prompt or its tools.**

This is what makes the study measure anything. If the agent can read the root cause —
directly, or via a hint shaped like the root cause — then it is not diagnosing, it is
recalling, and every efficiency number you collect is measuring the config file rather
than the model.

Enforcement, in order of strength:

1. `config_loader.DEFAULT_PATHS` has no entry for `ground_truth`, and
   `build_compiled_config()` never reads this directory. The compiled config the agent
   runs on cannot contain it.
2. `scripts/check_prompt_leakage.py` builds the **real** runtime system prompt for every
   scenario × every presentation condition and asserts that no answer token from any file
   here appears in it. It also scans the configs that *do* reach the prompt for
   **fault-shaped hints** — text that does not quote the answer but gives it away
   (worked examples built on a real scenario, discriminators that only make sense if you
   already know the fault). Run it in CI, and before every data-collection session.

A leak is not only a verbatim copy of `root_cause.statement`. These all count:

- a worked example in a behavior/foundation config built on a real scenario
- a rule that tells the agent which device to suspect for a symptom you actually inject
- a "prefer this check" hint that is only optimal because of the injected fault
- device knowledge naming a failure mode that is the answer to a scenario

## What goes where

| Belongs in the prompt | Belongs here (never in the prompt) |
|---|---|
| How the system is built (devices, protocols, what the dashboard shows) | Which of those things is broken this session |
| Domain-general strategy (bisect, device-before-rule) | The check sequence that solves *this* scenario |
| Stable physical facts (the TV is not a probe) | The fault injected into the TV path |

The line: **would this still be true if you had injected a different fault?** If yes, it
is system knowledge and may be in the prompt. If no, it is an answer and lives here.

## Filling one in

Copy `_TEMPLATE.yaml`. Every field marked `ASK_USER` is something only the person who
built the lab can supply — do not guess it, and do not infer it from Home Assistant,
because a fault injected *physically* (unplugged Zigbee module, taped-over sensor) leaves
no trace in HA at all.

`status: draft` until the values are confirmed against the real lab; `status: validated`
once a pilot run reproduced the symptom from the injected fault.

## Related

- `scripts/check_prompt_leakage.py` — the probe described above.
- `scripts/check_rule_drift.py` — keeps HA, `portal_rules.yaml`, and the participant UI
  telling the same story. Ground truth is only meaningful if those three agree.
