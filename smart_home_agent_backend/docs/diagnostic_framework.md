This is diagnistic theory layer.

It defines:
- diagnosis before fixing
- hypothesis testing
- user strategy detection
- device / connection / logic / context layers
- diagnostic mindmaps

This should not be used as normal “knowledge”. It should guide the reasoning strategy of the agent.


I will include this later if needed, now I want to see if it reasons better without fixating the mind map:

diagnostic_mindmaps:
  - id: device_level_diagnosis
    based_on: devices_first
    steps:
      - step: 1
        action: "Confirm device identity and expected behavior."
        reason: "Users often misidentify devices."

      - step: 2
        action: "Check physical state such as power or local response."
        reason: "Rule out local issues first."

      - step: 3
        action: "Check visibility in the system or app."
        reason: "Distinguish physical failure from digital/system issue."

      - step: 4
        action: "Check similar devices."
        reason: "Detect isolated versus systemic issue."

  - id: connection_level_diagnosis
    based_on: connections_first
    steps:
      - step: 1
        action: "Identify connection type."
        reason: "Different protocols have different dependencies."

      - step: 2
        action: "Check hub or router status."
        reason: "Hub/router may be a shared failure point."

      - step: 3
        action: "Compare multiple devices."
        reason: "Detect clustered failure."

      - step: 4
        action: "Check recent system changes."
        reason: "Recent changes often cause problems."

  - id: dependency_chain_diagnosis
    based_on: follow_the_thread
    steps:
      - step: 1
        action: "Identify expected trigger."
        reason: "This is the start of the causal chain."

      - step: 2
        action: "Check whether the trigger occurred."
        reason: "If the trigger did not occur, the action will not happen."

      - step: 3
        action: "Check intermediate logic."
        reason: "Rules or automations may block execution."

      - step: 4
        action: "Check final device response."
        reason: "Separates logic failure from device failure."

diagnostic_workflow:
  phase_1_symptom:
    description: "Capture the user’s reported symptom verbatim."
    rule: "Do not interpret it as the cause yet, and do not pick a mindmap."

  phase_2_primary_suspect:
    description: >
      Silently infer the primary suspect from the user’s natural language.
      Stored in state as primary_suspect_label and primary_suspect_type.
    rule: >
      Do NOT ask the user which strategy to use or where to start.
      Infer it from their words and proceed with diagnosis.
      If inference is ambiguous, keep primary_suspect_type=’unknown’.

  phase_3_guided_diagnosis:
    description: "Help the user check whatever they chose, one step at a time."
    requirements:
      - "Explain why each step matters."
      - "Let the user pick the next surface to inspect."
      - "Update hypotheses after each step."

  phase_4_strategy_observation:
    description: >
      On every turn, classify the user’s move and update strategy_signals.
      Derive observed_strategy from the running tally.
    rule: >
      This is a silent research label — it must never influence the conversation.
      Do not mention it, hint at it, or steer the user toward any strategy.
      The agent follows the user’s lead; the label is a consequence, not a guide.

  phase_5_issue_identification:
    description: "Conclude likely cause only after sufficient evidence."
    rule: "Only after sufficient evidence."

  phase_6_logging:
    description: >
      At session end (issue_identified=True, escalation, or reset),
      persist observed_strategy, strategy_history, and primary_suspect
      to the session log.
