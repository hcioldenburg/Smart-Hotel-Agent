// Starter chat hook for the smart_home_agent_backend frontend.
// Drop into: src/hooks/useChat.ts
//
// Depends on:
//   - src/config.ts exporting `API_BASE` (e.g. '/agent' with a Vite proxy to :8000)
//   - the ChatResponse / SessionMessage types from the codegen config (inline below
//     so this file is self-contained; move them to src/types.ts if you prefer)

import { useCallback, useRef, useState } from 'react';
import { API_BASE } from '../config';

// ─── Types (mirror the backend Pydantic models) ───
export interface VisualOutput {
  path?: string;
  data_url?: string | null;
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
  /** Expected answers to the current check, rendered as quick-reply buttons. */
  reply_options?: string[];
  visual_outputs: VisualOutput[];
  portal_context: Record<string, unknown>;
  evidence: Array<Record<string, unknown>>;
  checked_tools: Array<Record<string, unknown>>;
  checked_devices: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  /** Structured fields from the agent, attached to assistant turns. */
  meta?: ChatResponse;
}

interface UseChat {
  messages: ChatMessage[];
  /** Latest structured response (phase, evidence, visuals, …). */
  latest: ChatResponse | null;
  sessionId: string | null;
  loading: boolean;
  error: unknown;
  send: (message: string) => Promise<void>;
  reset: () => Promise<void>;
}

// ─── Hook ───
export function useChat(scenarioId?: string): UseChat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [latest, setLatest] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Ref so concurrent sends always read the freshest id without a render race.
  const sessionIdRef = useRef<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const send = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || loading) return;

      setMessages((m) => [...m, { role: 'user', content: text }]);
      setLoading(true);
      setError(null);

      try {
        const res = await fetch(`${API_BASE}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            session_id: sessionIdRef.current ?? undefined,
            active_scenario_id: scenarioId,
          }),
        });
        if (!res.ok) throw new Error(`chat failed: ${res.status}`);

        const data: ChatResponse = await res.json();

        sessionIdRef.current = data.session_id;
        setSessionId(data.session_id);
        setLatest(data);
        setMessages((m) => [...m, { role: 'assistant', content: data.reply, meta: data }]);
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [loading, scenarioId],
  );

  const reset = useCallback(async () => {
    const id = sessionIdRef.current;
    if (id) {
      try {
        await fetch(`${API_BASE}/reset/${id}`, { method: 'POST' });
      } catch {
        /* clearing local state below regardless */
      }
    }
    sessionIdRef.current = null;
    setSessionId(null);
    setMessages([]);
    setLatest(null);
    setError(null);
  }, []);

  return { messages, latest, sessionId, loading, error, send, reset };
}
