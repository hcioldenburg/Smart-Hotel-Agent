// Chat hook for the smart_home_agent_backend. Owns the conversation transport
// (POST /chat, POST /reset/{id}), the greeting seed, and the quick-reply
// starters. Diagnosis view models are derived from `latest` in lib/diagnosis.

import { useCallback, useRef, useState } from 'react';
import { API_BASE, ERROR_MESSAGE, GREETING, STARTERS } from '../config';
import { buildFloorMaps, cleanText } from '../lib/diagnosis';
import type { ChatMessage, ChatResponse } from '../types';

interface UseChat {
  messages: ChatMessage[];
  /** Latest structured response (phase, evidence, visuals, …). */
  latest: ChatResponse | null;
  sessionId: string | null;
  /** Quick-reply chips: present until the first send, restored on reset. */
  starters: string[];
  /**
   * Expected answers to the agent's current check (backend reply_options),
   * shown as answer buttons. Cleared the moment the user sends anything, so
   * buttons never outlive the question they belong to.
   */
  options: string[];
  typing: boolean;
  error: string;
  send: (message: string) => Promise<void>;
  reset: () => Promise<void>;
}

const greetingMessage: ChatMessage = { role: 'assistant', content: GREETING };

export function useChat(presentationMode?: string | null, scenarioId?: string): UseChat {
  const [messages, setMessages] = useState<ChatMessage[]>([greetingMessage]);
  const [latest, setLatest] = useState<ChatResponse | null>(null);
  const [starters, setStarters] = useState<string[]>([...STARTERS]);
  const [options, setOptions] = useState<string[]>([]);
  const [typing, setTyping] = useState(false);
  const [error, setError] = useState('');

  // Ref so concurrent sends always read the freshest id without a render race.
  const sessionIdRef = useRef<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const typingRef = useRef(false);

  // Keep the current condition in a ref so the send closure reads the freshest
  // choice without re-creating `send`. The backend only honours it on the first
  // turn of a session (locked thereafter).
  const presentationModeRef = useRef<string | null>(presentationMode ?? null);
  presentationModeRef.current = presentationMode ?? null;

  // The backend accumulates visual_outputs across the whole session, so each
  // response carries every image generated so far. Track which we've already
  // shown and attach only the new ones to each turn's bubble.
  const seenVisualsRef = useRef<Set<string>>(new Set());

  const send = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || typingRef.current) return;

      typingRef.current = true;
      setMessages((m) => [...m, { role: 'user', content: text }]);
      setStarters([]);
      setOptions([]);
      setTyping(true);
      setError('');

      try {
        const res = await fetch(`${API_BASE}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            session_id: sessionIdRef.current ?? undefined,
            active_scenario_id: scenarioId,
            presentation_mode: presentationModeRef.current ?? undefined,
          }),
        });
        if (!res.ok) throw new Error(`chat failed: ${res.status}`);

        const data: ChatResponse = await res.json();
        sessionIdRef.current = data.session_id;
        setSessionId(data.session_id);
        setLatest(data);

        const newVisuals = (data.visual_outputs ?? []).filter((v) => {
          const key =
            (typeof v.related_device_id === 'string' && v.related_device_id) ||
            v.path ||
            v.data_url ||
            '';
          if (!key || seenVisualsRef.current.has(key)) return false;
          seenVisualsRef.current.add(key);
          return true;
        });
        const floorMaps = buildFloorMaps(data, newVisuals);
        setMessages((m) => [
          ...m,
          { role: 'assistant', content: cleanText(data.reply ?? ''), meta: data, floorMaps },
        ]);
        setOptions(data.reply_options ?? []);
      } catch {
        setError(ERROR_MESSAGE);
      } finally {
        typingRef.current = false;
        setTyping(false);
      }
    },
    [scenarioId],
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
    typingRef.current = false;
    seenVisualsRef.current = new Set();
    setSessionId(null);
    setMessages([greetingMessage]);
    setLatest(null);
    setStarters([...STARTERS]);
    setOptions([]);
    setTyping(false);
    setError('');
  }, []);

  return { messages, latest, sessionId, starters, options, typing, error, send, reset };
}
