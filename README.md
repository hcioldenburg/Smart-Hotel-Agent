<div align="center">
  <img src="UOL_Logo.png" width="300">
</div>

# **Sherlock Home, an AI agent used in the 2026 studies following the Crosswire research. A joint effort between Mikolaj P. Wozniak and Pantea Sanei Ganjeh for their respective thesis'.**
 
[Crosswire](https://github.com/oldenburghci/Crosswire-MQTT-Interceptor) is an open-source toolkit for systematically inducing failures in operational smart home environments. It operates as a transparent MQTT proxy between connected devices and a Home Assistant hub, intercepting the message flow that constitutes the system's observable behaviour — without modifying device firmware or hub configuration.

During the studies shown in the Crosswire paper, participants had to troubleshoot faulty and/or misbehaving smart home devices and tell us which device(s) in particular was misbehaving and what a potential cause could be. To aid the participants in their troubleshooting efforts in the 2026 studies an AI agent was jointly developed between Mikolaj P. Wozniak and Pantea Sanei Ganjeh to be each be awarded with their desired academic degree. The agent could be used similarily to already established chatbots like ChatGPT, Claude, or Le Chat.

## Sherlock Home

Sherlock Home is a research-oriented conversational diagnostic agent for troubleshooting smart-home problems in a controlled smart-hotel/laboratory environment.

The project combines a **FastAPI backend**, a **LangGraph-based diagnostic agent**, a **React/TypeScript frontend**, structured smart-home knowledge and portal configuration, scenario-specific ground truth, and an evaluation framework for measuring diagnostic performance.

The central idea is to help a non-expert investigate a reported smart-home symptom step by step rather than immediately guessing a cause. The agent gathers evidence through tools, maintains hypotheses and diagnostic state, guides the user through checks, and concludes only after sufficient evidence has been collected.

The repository is designed not only to run the assistant, but also to support controlled research studies in which different scenarios and presentation conditions can be reproduced and evaluated.

## How it works

Sherlock Home sits between the participant and the smart-home environment as a conversational troubleshooting layer.

A typical interaction follows this flow:

```text
User reports a symptom
        ↓
Agent captures and normalizes the symptom
        ↓
Agent identifies an initial suspect / diagnostic direction
        ↓
Agent checks the environment using tools
        ↓
User performs or reports the requested check
        ↓
Agent updates evidence and hypotheses
        ↓
Evaluator audits the proposed response
        ↓
Agent revises once if necessary
        ↓
Agent identifies the issue or escalates
        ↓
Session and diagnostic data are logged
```

The system is intentionally designed around **diagnosis before fixing**. The agent should verify environment facts before making claims and should separate participant assumptions from verified evidence.

## Research and diagnostic principles

The backend documentation defines several core principles:

- **Diagnosis before fixing** — identify the likely cause before recommending a corrective action.
- **Hypothesis testing** — use observations and tool results to confirm or reject possible explanations.
- **User strategy detection** — observe how the participant approaches the diagnosis without steering them toward a particular strategy.
- **Step-by-step interaction** — request one useful check at a time and explain why it matters.
- **Device / connection / logic / context layers** — consider different levels of the smart-home system rather than fixating on the first visible symptom.
- **Evidence-grounded reasoning** — use tools to verify system facts whenever those facts can be checked.
- **Safe physical guidance** — do not instruct users to perform unsafe electrical or hardware inspections based only on software information.

The diagnostic framework describes several possible reasoning approaches, including device-level diagnosis, connection-level diagnosis, and dependency-chain diagnosis. The system is intended to infer the participant's approach from their behaviour rather than explicitly asking them to choose a strategy.

### Frontend

The frontend is a React 19 / TypeScript application built with Vite and Tailwind CSS.

Its main responsibilities are:

- Presenting the conversational diagnostic interface
- Displaying agent and user messages
- Showing diagnostic progress
- Displaying summaries and error information
- Presenting floor-map visualisations
- Providing quick replies
- Communicating with the FastAPI backend

The frontend communicates with the backend through the `/agent` API prefix during development.

### Backend

The backend is a Python application built around:

- FastAPI
- LangGraph
- LangChain
- Pydantic
- PyYAML
- SQLite
- OpenAI model integration

The FastAPI application is defined in:

```text
smart_home_agent_backend/api.py
```

The diagnostic graph is assembled in:

```text
smart_home_agent_backend/agent.py
```

The graph contains the following major nodes:

```text
START
  ↓
route
  ├── clarify → END
  ├── locate  → END
  └── solve
        ↓
      tools ─────┐
        ↑        │
        └────────┘
        ↓
      evaluate
        ├── revise → solve
        └── END
```

The evaluator is deliberately placed before the participant sees the generated response. A response can be revised once when the evaluator identifies a problem; the system does not permit an unbounded self-correction loop.

## Diagnostic tools

The agent has access to a set of tools for inspecting the smart-home environment:

- `get_space_info`
- `list_devices`
- `get_device_info`
- `get_device_knowledge`
- `check_hub_status`
- `check_device_connectivity`
- `show_device_location`
- `show_hub_location`
- `get_portal_overview`
- `list_portal_tasks`
- `get_portal_task_guide`
- `get_portal_entity_info`
- `get_device_portal_check_guide`

Tool usage is governed by:

```text
configs/tools/tool_policy.yaml
```

The general policy is to verify environment facts before making claims.

For example, when a specific device is involved, the agent should retrieve device information before diagnosing it and should check connectivity before concluding that a device has failed.

For multi-device failures, hub status becomes an important shared dependency.

## Tool usage order

The configured preferred order is:

```text
list_devices
      ↓
get_device_info
      ↓
check_device_connectivity
      ↓
check_hub_status
      ↓
show_device_location
      ↓
show_hub_location
```

This is not a requirement to call every tool. The agent is expected to select the smallest useful set of checks for the current diagnostic step.

The tool policy explicitly discourages:

- Making assumptions about device properties without checking them
- Skipping connectivity checks when connectivity is relevant
- Blaming an individual device when multiple devices are affected and the hub has not been checked
- Calling irrelevant tools
- Repeating unnecessary tool calls
- Suggesting unsafe physical actions

## **License & Citation**

Crosswire, as well as the AI agent Sherlock Home, is released under the **Apache 2.0 License**. If you use this AI agent in your work, we kindly ask you to **cite the original paper** and/or **link to this repository** to support its development and visibility. 

This repository will be maintained as a part of the ongoing research project, managed by Carl von Ossietzky Universität Oldenburg. For any alterations or additions to the interceptor, please either implement them yourself or fork this repository to add your changes, so others can benefit.

Should you have any questions, you're welcome to contact our current project lead: 

(June 2026 -- current) Mikołaj P. Woźniak, mikolaj.wozniak@uni-oldenburg.de
