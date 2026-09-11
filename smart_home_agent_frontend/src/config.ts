// Endpoints, hosts, and constants. Never inline these in components.

/** Relative API base — proxied to the FastAPI backend (see vite.config.ts). */
export const API_BASE = '/agent';

/**
 * Absolute backend origin. Empty by default, which means "same origin, via the
 * `/agent` proxy" — see backendAssetUrl. Only set VITE_API_ORIGIN when the
 * backend is genuinely somewhere else (e.g. a deployed server).
 */
export const API_ORIGIN: string = import.meta.env.VITE_API_ORIGIN ?? '';

/**
 * Resolve a site-relative image path the backend returns (`/assets/...`,
 * `/outputs/...`) into a URL the browser can load.
 *
 * These are routed through the SAME Vite proxy as the API: the `/agent` proxy
 * strips its prefix, so `/assets/x.png` → `/agent/assets/x.png` → backend
 * `/assets/x.png`. That avoids the collision with the frontend's own `/assets`
 * (public/assets/floorplans) without pointing the browser at the backend's port.
 *
 * Staying same-origin is what makes the app work from another device: a tablet
 * on the LAN only has to reach the dev server. Addressing the backend's port
 * directly used to hang (the host firewall blocks inbound :8000 even though the
 * dev server's port is open), which surfaced as a timeout.
 */
export function backendAssetUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${API_ORIGIN || API_BASE}${suffix}`;
}

/** First message Sherlock shows before any user input. */
export const GREETING =
  "I'm Sherlock, your smart-home diagnostics assistant. Tell me which device is misbehaving and we'll find the cause one check at a time. What's the trouble?";

/** Quick-reply starter chips (restored on "Start a new case"). */
export const STARTERS: readonly string[] = [
  'My fan stays on even though I turned the TV on',
  "A light won't respond to the switch",
  'Where is the smart fan?',
];

export const SUMMARY_NOTE =
  'For this study, applying the fix is out of scope — no further action is needed. You can report the finding to the studio admin.';

export const ERROR_MESSAGE =
  'Sherlock lost the thread (connection issue). Please try sending that again.';

/** PIN that unlocks the admin history panel. Demo-grade gate, not real auth. */
export const ADMIN_PIN = '0000';
