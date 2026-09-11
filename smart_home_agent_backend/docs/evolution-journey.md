# Sherlock.home — Agent Evolution Journey

*A working log of the design decisions, bugs, and iterations behind the smart-home
troubleshooting agent. Written as source material for the thesis (methodology, implementation,
and evaluation chapters). Covers the iteration block of 2026-07-16 → 2026-07-18.*

> **How to read this.** Sections 1–2 orient. Section 3 is the chronological journey (the meat —
> each entry is a symptom → diagnosis → fix → verification story). Sections 4–5 pull out the
> reusable principles and the evaluation method. Section 6 has the numbers. Section 7 is honest
> about what is NOT yet proven. Section 8 is a reproducibility index.

---

## 1. System under study

**Sherlock.home** is a conversational agent that guides a participant through diagnosing a
malfunction in a smart-hotel room, via a tablet "dashboard". It is the instrument of an HCI
study: the research question is about *human diagnostic reasoning* — how people localize and
classify a fault when guided — not about the agent answering for them.

Key properties that shape every design decision:

- **The agent guides; the participant observes and reports.** The agent cannot see the room or
  the dashboard. It asks the participant to run one check at a time and report back a value.
- **One injected fault per scenario**, of one of three classes: **device error**,
  **connection error**, **configuration error**. Correctly *classifying* the fault type is a
  graded outcome, not just localizing the faulty component.
- **Two presentation conditions** (a between-subjects variable): `dashboard` (grouped device
  cards) vs `floor_map` (spatial floor map with sub-tabs). They differ only in the Your-Room
  tab; All Devices / All Rules are identical.
- **Ground truth is never shown to the agent.** `configs/ground_truth/*.yaml` holds the answer
  key (root cause, fault class, discriminating checks, anticipated near-misses). Three guard
  scripts enforce this (see §5).
- **Domain quirks that are load-bearing** (each caused a real bug at some point):
  - *Three clocks* — only the dashboard's experiment clock decides whether time-gated rules
    fire; never tell the participant the clock is simulated.
  - *The TV is not a probe* — its command travels a different path than its reported state and
    is unacknowledged, so a non-responding TV is inconclusive; never use it for connectivity.
  - *Wall switches are not local device tests* — pressing one fires an automation, so it travels
    the same road as the dashboard. Only controls *on the unit* (fan buttons, a sensor's
    indicator light) are genuine local tests.
  - *Rule names are public, rule contents are secret* — the agent may cite a rule by name (the
    participant sees the name too) but must never claim what a rule does until the participant
    reads its card; per-rule device linkage is also withheld (it can give a scenario away).

## 2. Architecture at a glance

- **Orchestration:** LangGraph. Nodes: `route → {clarify | locate | solve} → tools (loop) →
  evaluate → END`.
  - `route` (`utils/router.py`): keyword-first intent classifier (symptom / followup / portal /
    location / greeting) → picks the mode.
  - `solve` (`utils/nodes.py`): the diagnostic core. Builds a large config-driven system prompt,
    calls the model, and emits a **`DiagnosticMove`** (a structured object: `action_type`,
    `reply_text`, `working_hypothesis`, ranked `hypotheses`, `reply_options`, `fault_label`).
  - `evaluate` (`utils/evaluator.py`): audits the move BEFORE the participant sees it (see §4).
- **Config-driven prompt** (`utils/config_loader.build_runtime_system_prompt`): assembled from
  YAML (environment, behavior, portal structure/tasks/rules/conditions, tool policy, foundation).
- **State** (`utils/state.py`): TypedDict carrying symptom, suspect, hypotheses board, evidence,
  deduction timeline, evaluator log, etc.
- **Surfaces:** FastAPI (`api.py`) + a React frontend (`smart_home_agent_frontend/`); sessions
  persisted in SQLite and mirrored to `logs/chats/*.json`.
- **Grading:** `eval/grade_sessions.py` scores logged sessions against ground truth.

---

## 3. The iteration journey (chronological)

Each entry: **trigger → what we found → what changed → how it was verified.**

### 3.1 Quick wins: indicator light + quick-reply buttons
- **Trigger:** first session review (`2f0b40e5`, graded clean: correct root cause + fault class).
  Two requests: the sensors' indicator light is green, not red; and add tappable answer buttons.
- **Changes:**
  - `configs/environment/Offis_smart_studio.yaml`: door + window sensor `location_hint`
    red → **green** (the only two real occurrences).
  - **Quick-reply buttons** (`reply_options`): the move now emits 2–4 expected readings ("Open /
    Closed / Unavailable") **only** for physical/portal checks with a *closed answer set*;
    open-ended questions get none. Backend normalizes (drops "Other"-like, dedupes, caps at 4,
    needs ≥2). Frontend renders them and appends its own dashed **"Other…"** chip that focuses the
    input (never sends) — so buttons are a hint, never a cage.
- **Design principle established:** buttons only where a valid closed set exists; the UI, not the
  model, owns the free-text escape hatch.

### 3.2 The "wandering / not-listening" session (`3535204e`)
- **Trigger:** a session the user (rightly) called bad — 12 turns, two "you're not listening"
  moments (participant: *"I just told you"*, then *"noooo"* at a re-asked check).
- **Root causes found (three distinct bugs):**
  1. **`clarify_node` leaked a real check.** The clarify prompt is supposed to only *ask*, but the
     model issued a portal check; because clarify ends at `END`, that check bypassed the evaluator
     AND the deduction timeline AND left `diagnosis_started` false — so the canned "gut feeling"
     opener fired a turn late, after a check had already run.
  2. **Duplicate-check blindness.** The evaluator only compared a new check against the
     *immediately preceding* one, so a re-ask with one turn in between sailed through. Also
     volunteered observations never landed in `evidence`, so "you already told me" was invisible.
  3. **`root_cause` locked from the rejected attempt.** Lock-on-first ran in `solve` *before* the
     evaluator audited, so the case file kept a conclusion the participant never saw.
- **Changes:** clarify prompt hardened + a deterministic backstop that records a leaked check as
  `last_check_requested`; duplicate check now compares against *all* answered timeline steps by
  requested-value phrase + device gate; suspect/prior-action answers now recorded as evidence;
  rejected conclusions rolled back on revise.

### 3.3 The rail-vs-chat divergence (`6b92ead4`, from a screenshot)
- **Trigger:** the case-file rail showed a check "Awaiting your reply…" that the chat never sent;
  the chat repeated the "take stock" fallback twice.
- **Root causes:** (a) `revision_count` only reset on *approved*, so after one block the counter
  stayed maxed and every later turn's first imperfection became an instant block → repeated
  fallback; (b) rejected moves left artifacts in state (a "ghost" open timeline step, stale
  `last_check_requested`); (c) a **false positive I had just introduced** in the duplicate check —
  it compared the new reply against `last_check_requested`, which `solve` had already overwritten
  with that same reply → guaranteed self-match, blocking every check on any touched device.
- **Changes:** on revise/block, roll back everything the rejected move wrote (root cause, timeline
  step, check pointers); reset the retry budget on block; duplicate check now matches against the
  instruction *embedded in the evidence record*, skips the agent's own tool lookups, and records
  the instruction at 300 chars so the requested-value comparison isn't truncated.
- **Lesson for the thesis:** the evaluator's own "revise" feedback loop was a rich source of
  subtle state-consistency bugs; each fix needed verification against the *actual logged state*,
  not a mock.

### 3.4 Rule-name visibility — added, then partly retracted
- **Added:** the agent is framed as familiar with the room, so it may name the one relevant rule
  outright ("open 'Door Window Open Fan Off'") instead of "the rule you think handles X"
  (`build_known_automations_block`, names only). Leakage/wiring guards updated; a duplicate YAML
  `warning:` key that had been silently shadowing real guidance was found and renamed.
- **Retracted (after `4abbad5c`):** giving all names invited *cross-rule fishing* — the agent read
  the Last-triggered of *sibling* rules as health proxies. That is **invalid in a study** (any
  sibling rule can be the injected fault in another scenario) and slow. Added the
  **no-sibling-baseline** rule and narrowed the names block to "open the ONE directly-responsible
  rule; check a device by its OWN signals."

### 3.5 Wrong fault class → the hypothesis engine + fault-type gate (`4abbad5c`)
- **Trigger:** the agent localized the right device (window sensor) but concluded
  **configuration_error** when ground truth is **device_error**, after wandering through sibling
  rules. It never ran the check that separates device / connection / configuration.
- **Structural finding:** the `hypotheses` / `rejected_hypotheses` state was **dead** — defined and
  rendered, but never written. The agent free-associated the next check each turn.
- **Changes (the biggest single architectural addition):**
  - **Ranked hypothesis board (Phase 10):** the move emits the full ranked list of candidate
    causes, each with its *cheapest discriminating check* and a status; `_merge_hypotheses`
    persists it and peels off rejected causes so a closed branch is never re-walked.
  - **Hard fault-type gate** (`evaluator._check_fault_type`): refuses a conclusion whose
    `fault_label` lacks its discriminating evidence — device needs a *local* check, connection
    needs *works-locally + system-blind*, configuration needs a rule read that *disagrees* with
    expectation. Both design forks (per-move board, hard block) were the user's explicit choice.
  - Policy rules: **classify the fault type before concluding**, **a matching rule is not a config
    fault**, **test a device by its own signals**.

### 3.6 The inert revise-tier (the loop's true root cause)
- **Trigger:** the first live simulated run looped: the agent localized the sensor, the new gate
  correctly blocked an unclassified conclusion, but then it *spun* on the "take stock" fallback.
- **Root cause (significant):** `evaluator_feedback` was **written but never read anywhere** — the
  "revise" retry re-ran `solve` on an unchanged prompt (temperature 0 → identical move) and always
  escalated to block. The entire revise tier had been inert for *every* check type.
- **Changes:** `solve` now injects the feedback as an imperative when `revision_count > 0`;
  fault-type feedback is device-aware (`_discriminator_hint`: sensor → indicator light, else
  buttons/switch); the block path has a participant-facing recovery
  (`_fault_type_recovery_message`) instead of the generic fallback.
- **Verification:** n=3 A/B on the looping scenario → 3/3 resolved, 3/3 correct fault class, no
  loop (was 0/1 to cap).

### 3.7 Building the evaluation harness
- **Motivation:** stop testing one conversation at a time by hand; run many, get a report.
- **`eval/simulate.py`** — an LLM **simulated participant** drives the graph end-to-end. The
  crucial design rule (mirror of the leakage guards): the sim-user is a **world oracle, fed
  observations only** — built from `evidence_path[].observable`, never `root_cause` / `mechanism`
  / `discriminates`. `_assert_no_leak` fails the build on any *verdict* language ("device error",
  "faulty", "misconfigured", "intercept"). Personas rotate for path diversity.
- **`eval/report.py`** — reuses `grade_sessions.grade()` (same rubric as real sessions), aggregates
  per-scenario resolved / root-cause / fault-class / near-miss + avg turns + avg transactions, and
  lists the runs worth a human's eye.
- Building the oracle forced out three latent leaks (`observable_in_room` and `per_condition` both
  contained the answer in prose; the guard's initial n-gram approach false-positived on shared
  device vocabulary → switched to verdict-phrase detection).

### 3.8 The cost/model saga
- **The wall:** every live sweep kept exhausting the OpenAI quota after ~9–18 runs.
- **Model switch to `gpt-5.6-terra`** (user's choice; sim-user on `gpt-5.4`). Two non-obvious
  Responses-API constraints surfaced, both now centralized in **`utils/llm.py`**:
  1. A reasoning model **cannot** combine function tools with a non-`none` `reasoning_effort` on
     chat-completions. The agent uses tools + structured output every turn, so it must run through
     the **Responses API** (reasoning ON) — validated with `bind_tools` + `with_structured_output`,
     not a bare call (a plain call doesn't trip it, which is how it was first missed).
  2. On the Responses API a message's `.content` is a **list of blocks** (reasoning + text), not a
     string — see 3.11.
- **`eval/preflight.py`** — three cheap calls that gate any sweep (it caught a wrong sim-model id
  and the quota state before spending on 36 runs).

### 3.9 Transaction reduction (the single-call move)
- **Finding:** a reply turn cost **~3 model calls** — a tool-decision call whose text was discarded,
  a second full `_generate_diagnostic_move` pass, and the evaluator. The two agent calls carried
  the same ~12K-token context and largely duplicated work.
- **Change (flag-gated, then default):** `DiagnosticMove` is bound as a *submit-tool* alongside the
  info tools — the model calls an info tool (→ loop) OR `DiagnosticMove` (→ done, structured fields
  from the args) in **one** call. Shipped behind `SMART_HOME_SINGLE_CALL_MOVE` (default OFF),
  A/B-validated, then made default.
- **A/B result (n=3, one scenario):** every core metric held (resolved / root-cause / fault-class
  all 3/3 both arms) while transactions dropped **42.0 → 22.3 calls/run (−47%)** — more than the
  mock's 2→1 predicted, because single-call also *reduced wandering*.

### 3.10 The partial-config gate loop (`TV-BEDLIGHT`)
- **Trigger:** the full-scenario optimized sweep showed SC-TV-ON-BEDLIGHT-AMBIENT looping to the
  cap (`fault_type_mismatch×12`). The agent correctly said "the rule only commands Bedlight Right"
  (an *incomplete-action* config fault) but the gate blocked it as "a matching rule isn't config."
- **Root cause:** the gate's "matching rule ≠ config" heuristic didn't distinguish a rule that
  *never ran* (the 4abbad5c trap → device) from one that *ran but did the wrong/incomplete thing*
  (→ genuine config).
- **Change:** block config **only** when the rule matches AND is *stale/never-fired*
  (`_RULE_STALE_RE`); broadened `_RULE_MISMATCH_RE` to catch partial actions ("only commands",
  "missing", "stays off"). Verified offline against both transcripts (TV-BEDLIGHT passes, 4abbad5c
  still blocks). Later confirmed live: TV-BEDLIGHT went **0/2 (looping) → 2/2 resolved, correct
  fault class, ~6 turns**.

### 3.11 The Responses-API content-block bug (`55d43900`, user-visible)
- **Trigger:** a real app session showed the agent's reply as a raw block-list repr — including
  the *encrypted reasoning* — instead of the clean sentence inside it.
- **Root cause:** regression from the reasoning-model switch — `response.content` is a list of
  blocks on the Responses API, and every `str(content)` in the code stringified it. Latent because
  single-call usually returns clean `reply_text`; surfaced when the model answered in text.
- **Change:** `utils/state.content_to_text()` extracts the text blocks (dropping reasoning/
  encrypted noise), applied at every content read (`solve`/`clarify` → `last_agent_reply`,
  `_serialize_messages`/`_message_content` → logs, `api._make_serializable` → persisted state +
  frontend). Pinned in `logic_checks`.

### 3.12 "Last-triggered first" (efficiency refinement, `8be76c3c`)
- **Trigger:** a clean, correct session (device_error, ~6 turns) — the only nit was that the agent
  asked the participant to transcribe a rule's full Triggers/Conditions/Actions *before* checking
  whether the rule had even fired.
- **Change (prompt rule):** **read a suspected rule's "Last triggered" FIRST.** If it hasn't fired
  when it should have, the rule's text is irrelevant — go straight upstream to the sensor/device
  the trigger watches. Read conditions/actions only in the other branch (fired but symptom
  persists). Saves a transcription step and dovetails with the fault-type gate.

---

## 4. Cross-cutting design principles (research-relevant)

- **The evaluator is a measurement instrument, not just a guardrail.** Every verdict — approvals
  included — is logged with the checks that failed, giving a *measured* hallucination / duplicate /
  premature-conclusion / fault-type-misfire rate rather than one reconstructed by reading
  transcripts. Its five checks: duplicate, affordance (can this device be operated?),
  evidence-sufficiency, upstream-not-ruled-out (don't blame a casualty), and LLM groundedness.
- **Leak discipline runs in two directions.** Ground truth never reaches the agent (three guard
  scripts, §5); and the *simulated participant* is fed observations only (same discipline applied
  to the test rig). A verdict-phrase guard enforces both.
- **Near-misses are scored separately, never as errors.** Several scenarios have an *anticipated*
  wrong answer that is reasonable from insufficient evidence (e.g. "connection error" on the
  device-fault sensor). Collapsing that into "wrong" would destroy the most interesting finding;
  `grade_sessions` reports it in its own column.
- **Structure persists, the LLM reasons, the evaluator audits.** The move schema drives state
  deterministically; the hypothesis board gives the reasoning a persistent, ranked memory; the
  evaluator gates conclusions. This division is what makes behavior measurable and debuggable.
- **Single fault per scenario + explicit fault-type classification** is the core reasoning demand,
  and the gate is what forces the agent to *earn* the classification with a discriminating check.

## 5. The evaluation infrastructure (a methodological contribution)

A **three-tier, cost-graded** workflow emerged, which is itself worth a thesis subsection:

| Tier | Command | Cost | Use |
|---|---|---|---|
| **Logic checks** | `python -m eval.logic_checks` | **Free** (no API) | Run on every change. 33 assertions pinning every fixed bug (fault-type gate, duplicate check, single-call routing, reply options, model detection, content extraction, leak guard, scripted oracle). |
| **Scripted sweep** | `python -m eval.simulate --scripted` | Cheap + **reproducible** | Deterministic participant answers from `evidence_path` observations (no sim-user calls, ~⅓ fewer transactions, run once). Faithful-by-construction: can only return ground-truth observations. |
| **LLM sweep** | `python -m eval.simulate` | Expensive | Occasional — persona-distribution realism. |

Plus **`eval/preflight.py`** (validate model config in 3 calls) and **`eval/grade_sessions.py`**
(the shared rubric: root-cause keywords, fault class, near-miss, efficiency vs `min_discriminating_checks`, evaluator-catch histogram).

**Cost levers implemented:**
- *Single-call move* — ~½ the agent calls (§3.9).
- *Prefix caching* — the runtime prompt was reordered into a **byte-identical ~10K-token stable
  prefix** + a small volatile suffix (`build_runtime_system_prompt(..., split=True)`), so
  OpenAI/Anthropic cache the prefix (~55% input-cost cut). Verified free: the stable prefix is
  identical across two very different turn-states.
- *Cheaper sim-user model* + *scripted participant* (no sim calls at all).

**The guard scripts** (`scripts/`): `check_prompt_leakage` (no ground-truth phrase reaches any
agent surface), `check_config_wiring` (every config key is either read or declared intentionally
unread), `check_rule_drift` (HA rules ≡ portal rules ≡ UI). All three gate every prompt/config
change.

## 6. Measured results (honest, with sample sizes)

- **Single-call A/B** (n=3, SC-DOOR-WINDOW-OPEN-FAN-OFF / dashboard): resolved 3/3 vs 3/3,
  root-cause 3/3 vs 3/3, fault-class 3/3 vs 3/3; transactions **42.0 → 22.3 / run**; avg turns
  15.0 → 9.7. Quality held; ~47% fewer transactions.
- **Full-scenario optimized sweep** (18 runs, LLM participant, n=1/cell): resolved **15/18**,
  root-cause 9/18, fault-class 11/18, avg **26.4 calls/run**. Strong on device/config faults
  (DOOR-WINDOW, TEMP-LOW-HEATER), weak on the hardest (TV-BEDLIGHT *before* its fix,
  WINDOW-CLOSED-FAN).
- **Scripted sweep** (14/18 completed before quota, n=1/cell): resolved 11/14, fault-class 9/14,
  avg **22.1 calls/run**. **TV-BEDLIGHT 0/2 → 2/2** (correct class) after the gate fix — the
  targeted regression this sweep existed to confirm. Absolute numbers here are *pessimistic*
  (confounded by the deterministic participant's neutral fallbacks on uncovered checks).
- **Prefix-cache prerequisite:** stable prefix byte-identical across turns (verified; the actual
  cache *hit* — `usage.cached_tokens` — is not yet measured live).
- **Claude-API cost estimate** (measured drivers: ~10.4K-token system prompt × ~24 calls/run):
  full 36-run sweep ≈ **$68 Opus / $28 Sonnet-5** uncached, roughly **half** with prefix caching;
  per-token pricing exact, token volume estimated ±40%.

## 7. Known limitations & open items (future work)

- **The cross-scenario A/B is incomplete.** The baseline (two-call) arm never ran across all
  scenarios — quota died. So "single-call holds quality *across fault types*" is supported by one
  scenario's clean A/B + a full optimized sweep, not a paired comparison.
- **Absolute quality on the hardest scenarios is unconfirmed** at adequate N. WINDOW-CLOSED-FAN
  (plug/power) and a few fault-class misses (MORNING-ROUTINE, EVENING-WIND-DOWN root-cause
  keywords) need more runs — affordable now that cost is ~4× lower, but not yet done.
- **The scripted participant's matcher** falls back to a neutral non-answer on ~14% of checks
  (those outside `evidence_path`), which under-serves the agent and depresses scripted-sweep
  quality numbers. Improving the matcher (lower threshold / device keying) is free, offline work.
- **The prefix cache hit is not yet measured** (needs a live 2-call check of `cached_tokens`).
- **The near-miss keyword matcher** occasionally false-positives (a correct "device / window
  sensor" conclusion shares words with a known false conclusion). Worth tightening before final
  grading.
- **N=1 per cell** in the full sweeps is a coverage check, not a distribution — read divergences
  as "look here," not verdicts.

## 8. File-by-file change index (reproducibility)

- `configs/environment/Offis_smart_studio.yaml` — sensor indicator light red → green.
- `utils/state.py` — enriched `HypothesisItem` (discriminating_check, rank); `content_to_text()`;
  `reply_options` field.
- `utils/nodes.py` — hypothesis board persistence (`_merge_hypotheses`); single-call move
  (`_SINGLE_CALL_MOVE`, submit-tool, `_move_from_tool_args`); wired `evaluator_feedback` into the
  retry; rejected-move rollback + counter reset; fault-type recovery message; clarify-leak
  backstop; `content_to_text` at content reads; split-prompt assembly (stable-first).
- `utils/evaluator.py` — fault-type gate (`_check_fault_type`, `_RULE_STALE_RE`,
  partial-action mismatch); duplicate check rewrite (all-timeline, value-phrase, embedded
  instruction); `_discriminator_hint`.
- `utils/config_loader.py` — `build_known_automations_block`; hypothesis-board render; the
  reasoning-policy rule cluster (no-sibling-baseline, own-signals, classify-fault-type,
  matching-rule-not-config, **Last-triggered-first**); **split=True** stable/volatile prompt.
- `utils/llm.py` — reasoning-model helper (Responses API / effort), call counter, `content_to_text`
  consumers.
- `utils/router.py` — (unchanged core; referenced for intent classification).
- `agent.py`, `api.py` — `content_to_text` in serialization/persistence; `reply_options` surfaced.
- `configs/portal/portal_tasks.yaml` — rule-inspection guidance; fixed duplicate `warning:` key.
- `scripts/check_*.py` — guard declarations updated for the names-only rule wiring; leak guard
  sanctions public rule names.
- `eval/simulate.py` — simulated + **scripted** participant, world-oracle fact-sheet, leak guard,
  call counting.
- `eval/report.py` — aggregation incl. avg transactions.
- `eval/preflight.py` — model-config validation.
- `eval/logic_checks.py` — free offline regression suite (33 assertions).
- `frontend/src/…` — `QuickReplies` "Other…" chip, `useChat` options, `InputBar` focus handle.

---

*Environment note: agent + evaluator on `gpt-5.6-terra` (reasoning, via the Responses API);
simulated participant on `gpt-5.4`. Model set once via `SMART_HOME_MODEL` in `.env`.*
