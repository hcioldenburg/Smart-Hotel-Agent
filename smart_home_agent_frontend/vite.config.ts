import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The backend (smart_home_agent_backend, FastAPI on :8000) exposes its routes at
// the root (/chat, /session, ...) and mounts its images at /assets and /outputs.
// We proxy `/agent/*` to it and strip the prefix, so the app talks to a single
// relative API base (see src/config.ts) — including images, which resolve to
// `/agent/assets/...`. The `/agent` prefix also sidesteps the collision with the
// frontend's own /assets (public/assets/floorplans).
//
// Everything therefore goes through THIS server on ONE port. That's what lets a
// tablet on the LAN use the app: it only has to reach the dev server, never the
// backend's port directly (inbound :8000 is typically blocked by the host
// firewall, which showed up as a timeout).
//
// 127.0.0.1, not localhost: on Windows `localhost` can resolve to IPv6 ::1 while
// uvicorn binds IPv4, and the proxy then stalls.
const BACKEND = 'http://127.0.0.1:8000';

const proxy = {
  '/agent': {
    target: BACKEND,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/agent/, ''),
    // An agent turn is an LLM call — well past the 2 min default.
    timeout: 300000,
    proxyTimeout: 300000,
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true, // allow access from other devices on the network (e.g. an iPad)
    // Fixed port so it never collides with the Smart Hotel UI (5172 = condition 1,
    // 5173 = condition 2). strictPort makes it fail loudly instead of drifting.
    port: 5174,
    strictPort: true,
    proxy,
  },
  // `vite preview` does NOT inherit server.proxy — without this, a built app
  // served to the tablet would have no backend at all.
  preview: {
    host: true,
    port: 5174,
    strictPort: true,
    proxy,
  },
});
