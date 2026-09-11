// Admin history drawer. Slides in from the right edge of the panel and lists
// every stored session (GET /sessions). Clicking a session expands its full
// transcript (GET /session/{id}). Gated behind AdminGate; see App.

import { useCallback, useEffect, useState } from 'react';
import { API_BASE } from '../../config';
import { buildSteps, buildTracker } from '../../lib/diagnosis';
import type { ChatResponse, SessionListItem, SessionStateResponse } from '../../types';
import Tracker from '../rail/Tracker';

interface Props {
  onClose: () => void;
}

function formatWhen(value?: string): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Case-file summary appended to the end of a transcript, reusing the live
 *  rail's tracker so the admin view matches what the guest saw. */
function CaseFileBlock({ detail }: { detail: SessionStateResponse }) {
  // SessionStateResponse omits `reply`; buildTracker only reads it as a last
  // resort for the root cause (now a real field), so an empty stand-in is safe.
  const tracker = buildTracker({ ...detail, reply: '' } as ChatResponse);
  const hasCase = !!(tracker.symptom || tracker.steps.length || tracker.cause);
  if (!hasCase) return null;

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--c-border-soft)' }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: '.4px',
          textTransform: 'uppercase',
          color: 'var(--c-faint)',
          marginBottom: 8,
        }}
      >
        Case file
      </div>
      <Tracker steps={buildSteps(tracker)} />
    </div>
  );
}

function SessionCard({ item }: { item: SessionListItem }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<SessionStateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !detail && !loading) {
      setLoading(true);
      setFailed(false);
      try {
        const res = await fetch(`${API_BASE}/session/${item.session_id}`);
        if (!res.ok) throw new Error(String(res.status));
        setDetail((await res.json()) as SessionStateResponse);
      } catch {
        setFailed(true);
      } finally {
        setLoading(false);
      }
    }
  };

  const solved = item.issue_identified;

  return (
    <div
      style={{
        border: '1px solid var(--c-border-card)',
        borderRadius: 14,
        background: 'var(--c-card)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={toggle}
        className="flex flex-col"
        style={{
          width: '100%',
          textAlign: 'left',
          gap: 7,
          border: 'none',
          background: 'transparent',
          padding: 14,
          cursor: 'pointer',
        }}
      >
        <div className="flex items-center justify-between" style={{ gap: 8 }}>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--c-body)',
              lineHeight: 1.4,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
            }}
          >
            {item.reported_symptom || 'No symptom recorded'}
          </span>
          <span
            style={{
              flex: 'none',
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: '.3px',
              textTransform: 'uppercase',
              padding: '3px 8px',
              borderRadius: 999,
              color: solved ? '#2f6b48' : 'var(--c-gold-label)',
              background: solved ? 'rgba(91,174,122,.16)' : 'var(--c-accent-panel)',
              border: `1px solid ${solved ? 'rgba(91,174,122,.35)' : 'var(--c-accent-brd)'}`,
            }}
          >
            {solved ? 'Solved' : 'Open'}
          </span>
        </div>
        <div
          className="flex items-center"
          style={{ gap: 8, fontSize: 11.5, color: 'var(--c-faint)', fontWeight: 500 }}
        >
          <span>{formatWhen(item.updated_at)}</span>
          {item.diagnosis_phase && (
            <>
              <span>·</span>
              <span style={{ textTransform: 'capitalize' }}>
                {item.diagnosis_phase.replace(/_/g, ' ')}
              </span>
            </>
          )}
        </div>
      </button>

      {open && (
        <div
          style={{
            borderTop: '1px solid var(--c-border-soft)',
            padding: 14,
            background: 'var(--c-chat)',
          }}
        >
          {loading && (
            <div style={{ fontSize: 12, color: 'var(--c-muted2)' }}>Loading transcript…</div>
          )}
          {failed && (
            <div style={{ fontSize: 12, color: 'var(--c-err-text)' }}>
              Couldn't load this session.
            </div>
          )}
          {detail && (
            <div className="flex flex-col" style={{ gap: 10 }}>
              {detail.messages.filter((m) => m.role === 'user' || m.role === 'assistant').length ===
              0 ? (
                <div style={{ fontSize: 12, color: 'var(--c-muted2)' }}>No messages.</div>
              ) : (
                detail.messages
                  .filter((m) => m.role === 'user' || m.role === 'assistant')
                  .map((m, i) => (
                    <div key={i} className="flex flex-col" style={{ gap: 3 }}>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: '.4px',
                          textTransform: 'uppercase',
                          color: m.role === 'user' ? 'var(--c-gold-label)' : 'var(--c-faint)',
                        }}
                      >
                        {m.role === 'user' ? 'Guest' : 'Sherlock'}
                      </span>
                      <span
                        style={{
                          fontSize: 12.5,
                          color: 'var(--c-body)',
                          lineHeight: 1.5,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {m.content}
                      </span>
                    </div>
                  ))
              )}
              <CaseFileBlock detail={detail} />
              <div
                style={{
                  marginTop: 4,
                  fontSize: 10.5,
                  color: 'var(--c-faint2)',
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                {item.session_id}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminPanel({ onClose }: Props) {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/sessions`);
      if (!res.ok) throw new Error(String(res.status));
      setSessions((await res.json()) as SessionListItem[]);
    } catch {
      setError('Could not load session history.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div
      onClick={onClose}
      style={{ position: 'absolute', inset: 0, zIndex: 30, animation: 'shFade .18s ease both' }}
    >
      {/* Dim the app behind the drawer */}
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(37,36,42,.28)' }} />

      <aside
        onClick={(e) => e.stopPropagation()}
        className="sh-scroll flex flex-col"
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          bottom: 0,
          width: 360,
          maxWidth: 'calc(100% - 32px)',
          background: 'var(--c-panel)',
          borderLeft: '1px solid var(--c-border)',
          boxShadow: '-20px 0 50px rgba(40,38,32,.18)',
          overflowY: 'auto',
          animation: 'shSlideIn .24s cubic-bezier(.22,.61,.36,1) both',
        }}
      >
        <div
          className="flex items-center justify-between"
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 1,
            padding: '20px 20px 14px',
            background: 'var(--c-panel)',
            borderBottom: '1px solid var(--c-border)',
          }}
        >
          <div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '.5px',
                textTransform: 'uppercase',
                color: 'var(--c-faint)',
              }}
            >
              Admin Panel
            </div>
            <div
              style={{
                fontFamily: "'Space Grotesk', sans-serif",
                fontWeight: 600,
                fontSize: 18,
                color: 'var(--c-ink)',
                marginTop: 2,
              }}
            >
              Session history
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close admin panel"
            className="flex items-center justify-center"
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              border: '1px solid var(--c-border-chip)',
              background: 'var(--c-chat)',
              color: 'var(--c-muted)',
              fontSize: 16,
              lineHeight: 1,
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        <div className="flex flex-col" style={{ gap: 10, padding: 20 }}>
          {loading && (
            <div style={{ fontSize: 13, color: 'var(--c-muted2)' }}>Loading history…</div>
          )}
          {error && (
            <div style={{ fontSize: 13, color: 'var(--c-err-text)' }}>
              {error}{' '}
              <button
                type="button"
                onClick={load}
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--c-gold-label)',
                  fontWeight: 600,
                  cursor: 'pointer',
                  padding: 0,
                }}
              >
                Retry
              </button>
            </div>
          )}
          {!loading && !error && sessions.length === 0 && (
            <div style={{ fontSize: 13, color: 'var(--c-muted2)' }}>No sessions recorded yet.</div>
          )}
          {sessions.map((s) => (
            <SessionCard key={s.session_id} item={s} />
          ))}
        </div>
      </aside>
    </div>
  );
}
