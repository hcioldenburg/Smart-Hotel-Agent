# Sherlock.home — Frontend

AI-powered smart-home **diagnostic** assistant for the OFFIS Smart Hotel Studio.
A chat UI that guides the user through troubleshooting one check at a time, with a
live case tracker and inline floor-map bubbles. The agent diagnoses; it never
controls or fixes devices.

This is a faithful implementation of the `design_handoff_sherlock_home` reference,
wired to the real `smart_home_agent_backend` (FastAPI on `:8000`).

## Stack

React 19 · TypeScript (strict) · Vite 6 · Tailwind CSS v4 · native `fetch`.
Fonts: Space Grotesk (display) + Manrope (body). No component library.

## Run

```bash
npm install
npm run dev      # http://localhost:5173
```

Start the backend separately on `:8000`. Vite proxies `/agent/*` → the backend
(prefix stripped), so the app talks to a single relative base. To point at a
different backend host for image URLs, set `VITE_API_ORIGIN`.

```bash
npm run build      # tsc -b + vite build
npm run typecheck  # types only
```

## Architecture

```
src/
  config.ts                # API base/origin, greeting, starters, copy
  types.ts                 # backend models + UI view models
  data/devices.ts          # device id → name + location hint (floor maps)
  lib/
    icons.tsx              # brand SVGs (magnifying glass, pin, send, check)
    diagnosis.ts           # ChatResponse → tracker/steps/summary/floor maps
  hooks/useChat.ts         # transport: POST /chat, /reset; greeting + starters
  components/
    Header.tsx
    chat/                  # MessageList, Agent/User messages, FloorMapBubble,
                           # TypingIndicator, QuickReplies, InputBar
    rail/                  # CaseRail, Tracker, SummaryCard, ErrorBanner
  App.tsx                  # layout + view-model wiring
```

### Backend mapping

The design prototype invented a structured `@@{...}` state line; the real backend
returns the fields directly. `lib/diagnosis.ts` adapts `ChatResponse`:

| UI concept        | Backend source                                            |
|-------------------|-----------------------------------------------------------|
| Tracker stage     | `diagnosis_phase` (mapped) / `issue_identified` → solved  |
| Symptom           | `reported_symptom`                                         |
| Prime suspect     | `target_device` / `target_device_type`                    |
| Root cause        | `portal_context.root_cause` → else concluding `reply`     |
| Floor-map bubbles | `visual_outputs` (prefers `data_url`, else `path`)        |

Floor-map device names/captions come from `data/devices.ts`, keyed by the device
id parsed from the visual path or `target_device_id`. Local copies of the plans
live in `public/assets/floorplans/` as a fallback when the backend omits an image.

Visible replies are stripped of stray Markdown (`cleanText`) to keep bubbles clean.
