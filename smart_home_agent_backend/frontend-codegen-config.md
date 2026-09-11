# Frontend Code-Generation Config

Paste this block **above your design specs** when asking an LLM to generate frontend
code. It encodes the stack, conventions, design tokens, and the real backend API so
generated components drop into the existing codebase without rework.

Conventions are derived from the sibling project `Smart_Home_UI_Rework`. The data
layer targets **this** repo's backend (`smart_home_agent_backend`, FastAPI on `:8000`).

---

## 1. Stack (match exactly)

| Concern        | Choice                                                        |
|----------------|---------------------------------------------------------------|
| Framework      | **React 19** (function components, hooks)                      |
| Language       | **TypeScript** `~5.8`, `strict: true`, ESM (`"type":"module"`) |
| Build / dev    | **Vite 6** (`@vitejs/plugin-react`)                           |
| Styling        | **Tailwind CSS v4** (`@tailwindcss/vite`) + inline `style` for exact tokens |
| Design tokens  | CSS custom properties in `src/index.css` (`var(--c-*)`)        |
| Icons          | `lucide-react`; `@mui/icons-material` where needed             |
| Canvas/diagram | `konva` + `react-konva` (only for floor-map / node graphs)     |
| HTTP           | native `fetch` for simple calls, `axios` for multi-step flows  |
| Fonts          | `Manrope` (body), `Space Grotesk` (display) — Google Fonts     |

Do **not** introduce a component library (no shadcn/MUI components, no styled-components).
Build components from scratch using semantic markup + Tailwind + the tokens below.
`@mui/material`/`@emotion` exist in the tree but are legacy — prefer plain elements.

---

## 2. Project structure (where generated files go)

```
src/
  components/            # shared components
    primitives/          # lowest-level: Toggle, VerticalBar, CircularArcDial
    controls/            # composite inputs: ArcDial, DragBar, SensorBadge, ToggleTile
    <Feature>Card.tsx    # feature cards live at components/ root or a feature subfolder
  pages/                 # one file per route/view (PascalCase, e.g. AllDevices.tsx)
  hooks/                 # useXxx.ts — data + behavior hooks
  lib/                   # pure helpers, icon maps (.tsx if they return JSX)
  data/                  # static seed/config data
  config.ts             # endpoints, headers, constants
  index.css             # design tokens + global resets + keyframes
  main.tsx / App.tsx
```

Place new files by role: a reusable input → `primitives/` or `controls/`; a screen → `pages/`;
a data fetch → `hooks/`. Keep pure logic out of components.

---

## 3. Component conventions

- **Default export**, named `function ComponentName(props: Props)`. No `React.FC`.
- Props typed via a local `interface Props { ... }` declared directly above the component.
- Keep components small and presentational; lift fetching into a `useXxx` hook.
- Optimistic UI: set local state first, fire the request, revert in `catch`.
- Tailwind classes for **layout/spacing/flex**; inline `style={{}}` for **exact colors,
  font sizes, radii, and pixel dimensions** that come from the design tokens.

```tsx
interface Props {
  label: string;
  active: boolean;
  onToggle: () => void;
}

export default function DeviceTile({ label, active, onToggle }: Props) {
  return (
    <div
      className="flex flex-col items-center gap-3 p-4 rounded-2xl cursor-pointer select-none"
      style={{ background: 'var(--c-card)', border: '1px solid var(--c-border)', minWidth: 120 }}
      onClick={onToggle}
    >
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--c-ink)' }}>{label}</div>
      <div style={{ fontSize: 11, color: 'var(--c-quiet)' }}>{active ? 'On' : 'Off'}</div>
    </div>
  );
}
```

Hooks return a consistent shape:

```ts
export function useThing(): { data: Thing[]; loading: boolean; error: unknown } { ... }
```

---

## 4. Design tokens (use the CSS vars, don't hardcode hex)

Warm, cream/beige palette with amber accent. Reference as `var(--c-*)`; if a token is
missing, add it to `:root` in `index.css` rather than inlining a new hex.

```css
/* Surfaces */
--c-bg:        #DCD6CA;  /* app canvas        */
--c-panel:     #F4F1EA;  /* content panel/topbar */
--c-card:      #FFFFFF;  /* cards             */
--c-chip:      #ECE6D9;  /* warm chip         */
--c-row-sel:   #FBF4E6;  /* selected row      */

/* Rail (dark sidebar) */
--c-rail:        #3E3D44;
--c-rail-active: #F4F1EA;

/* Amber / accent */
--c-amber:       #E0992F;
--c-amber-hover: #E7A53A;
--c-amber-light: #F6E6C9;
--c-amber-brd:   #EAD3A4;
--c-amber-deep:  #5C3F12;

/* Text */
--c-ink:      #25242A;  --c-muted:    #6F6A60;
--c-quiet:    #8A857A;  --c-disabled: #C7C3BB;

/* Borders */
--c-border:   #ECE6D9;  --c-border2:  #E5DFD2;

/* Sensor / status */
--c-teal:     #5BAE7A;  --c-teal-ring: rgba(47,158,150,0.45);
--c-rule-on:  #2E7D52;  --c-rule-on-bg: #E3F1E8;  --c-batt-low: #C0552F;

/* Rule blocks */
--c-trigger-bg: #FBF1DE; --c-trigger-brd: #F2E3C4;
--c-cond-bg:    #FBF6EC; --c-cond-brd:    #F0E4CC;
```

**Shape / type scale**
- Cards: `rounded-2xl` (≈16px); chips/pills: `rounded-full`.
- Base font 15px; card titles ~13px/600; captions ~11px/`--c-quiet`.
- Body font `Manrope`; numeric/display `Space Grotesk`.
- Toggle = amber pill (`.hw-toggle` class already in `index.css`).
- Slim 4px scrollbars; `accent-color: var(--c-amber)` on range inputs.

---

## 5. Backend API (this is what components call)

`smart_home_agent_backend` — FastAPI, base URL `http://localhost:8000`.
No auth header required; CORS is open to any `localhost` port. In dev, proxy `/api`
(or call the absolute base) via `vite.config.ts`:

```ts
server: {
  proxy: { '/agent': { target: 'http://localhost:8000', changeOrigin: true } },
}
```

Put the base URL in `src/config.ts` (e.g. `export const API_BASE = '/agent'`), never inline.

### Endpoints

| Method | Path                    | Body / params                                   | Returns                |
|--------|-------------------------|--------------------------------------------------|------------------------|
| POST   | `/chat`                 | `{ message, session_id?, active_scenario_id? }`  | `ChatResponse`         |
| GET    | `/session/{session_id}` | —                                                | `SessionStateResponse` |
| GET    | `/sessions`             | —                                                | `SessionListItem[]`    |
| POST   | `/reset/{session_id}`   | —                                                | `{ status, session_id }` |
| GET    | `/health`               | —                                                | health object          |
| —      | `/assets/...`, `/outputs/...` | static image files                         | image bytes            |

This backend is a **conversational diagnostic agent**, not a device CRUD API. The core
loop is: POST a user message to `/chat`, render `reply`, and surface the structured
diagnostic fields (phase, evidence, visuals) alongside the chat.

### Response types (mirror the Pydantic models)

```ts
export interface VisualOutput {
  path?: string;
  data_url?: string | null;   // inline base64 — prefer this for <img src>
  exists?: boolean;
  [k: string]: unknown;
}

export interface ChatResponse {
  session_id: string;
  reply: string;

  diagnosis_phase?: string | null;
  next_action_type?: string | null;

  reported_symptom?: string | null;
  target_device?: string | null;
  target_device_id?: string | null;
  target_device_type?: string | null;

  last_check_requested?: string | null;
  pending_question?: string | null;
  issue_identified: boolean;

  visual_outputs: VisualOutput[];
  portal_context: Record<string, unknown>;
  evidence: Array<Record<string, unknown>>;
  checked_tools: Array<Record<string, unknown>>;
  checked_devices: string[];
}

export interface SessionMessage { role: string; content: string; tool_calls?: unknown }

export interface SessionStateResponse extends Omit<ChatResponse, 'reply'> {
  messages: SessionMessage[];
}

export interface SessionListItem {
  session_id: string;
  created_at: string;
  updated_at: string;
  reported_symptom?: string | null;
  diagnosis_phase?: string | null;
  issue_identified: boolean;
  observed_strategy?: string | null;
  scenario_id?: string | null;
}
```

### Rendering rules
- **Images**: use `visual.data_url` when present; else fall back to `` `${API_BASE_HOST}${visual.path}` `` (served from `/assets` or `/outputs`).
- **Session**: keep `session_id` from the first `/chat` reply in state; send it on every
  subsequent message. Load history with `/session/{id}`; clear with `/reset/{id}`.
- Treat all `*_?: ... | null` fields as optional in the UI — guard before rendering.

### Hook pattern for the chat call

```ts
import { API_BASE } from '../config';

export async function sendChat(
  message: string,
  sessionId?: string,
  scenarioId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId, active_scenario_id: scenarioId }),
  });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);
  return res.json();
}
```

---

## 6. Output expectations for generated code

- TypeScript, no `any` in public signatures (the repo uses `any` internally but prefer
  precise types in new code; `strict`, `noUnusedLocals`, `noUnusedParameters` are on).
- One component per file, default-exported, with a local `Props` interface.
- Tailwind for layout + `var(--c-*)` tokens for color; no new hardcoded hex.
- No new dependencies unless the spec explicitly calls for one.
- Don't embed secrets or tokens; read config from `src/config.ts`.
- Accessible defaults: real `<button>`/`<label>`, keyboard-operable toggles, `alt` on images.

---

*Append your design spec / screenshot / Figma reference below this line.*
