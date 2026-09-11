// PIN gate for the admin history panel. A modal overlay that asks for the
// admin PIN; on the correct PIN it calls onUnlock, otherwise it surfaces an
// inline error. This is a demo-grade gate (see ADMIN_PIN), not real auth.

import { useEffect, useRef, useState } from 'react';
import { ADMIN_PIN } from '../../config';

interface Props {
  onUnlock: () => void;
  onCancel: () => void;
}

export default function AdminGate({ onUnlock, onCancel }: Props) {
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = () => {
    if (pin === ADMIN_PIN) {
      onUnlock();
    } else {
      setError('Incorrect PIN. Try again.');
      setPin('');
      inputRef.current?.focus();
    }
  };

  return (
    <div
      onClick={onCancel}
      className="flex items-center justify-center"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 40,
        background: 'rgba(37,36,42,.42)',
        backdropFilter: 'blur(2px)',
        animation: 'shFade .18s ease both',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 320,
          maxWidth: 'calc(100% - 40px)',
          background: 'var(--c-panel)',
          border: '1px solid var(--c-border)',
          borderRadius: 18,
          padding: 24,
          boxShadow: '0 24px 60px rgba(40,38,32,.32)',
          animation: 'shPop .22s ease both',
        }}
      >
        <div
          style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 700,
            fontSize: 17,
            color: 'var(--c-ink)',
          }}
        >
          Admin access
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--c-muted2)', marginTop: 4, fontWeight: 500 }}>
          Enter the admin PIN to view session history.
        </div>

        <input
          ref={inputRef}
          type="password"
          inputMode="numeric"
          autoComplete="off"
          value={pin}
          onChange={(e) => {
            setPin(e.target.value);
            if (error) setError('');
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
            if (e.key === 'Escape') onCancel();
          }}
          placeholder="• • • •"
          className="sh-in"
          style={{
            marginTop: 16,
            width: '100%',
            textAlign: 'center',
            letterSpacing: '8px',
            fontSize: 20,
            padding: '11px 12px',
            borderRadius: 12,
            border: `1px solid ${error ? 'var(--c-err-brd)' : 'var(--c-border-input)'}`,
            background: 'var(--c-chat)',
            color: 'var(--c-ink)',
            outline: 'none',
          }}
        />

        {error && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--c-err-text)', fontWeight: 600 }}>
            {error}
          </div>
        )}

        <div className="flex" style={{ gap: 10, marginTop: 18 }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              flex: 1,
              border: '1px solid var(--c-border-chip)',
              background: 'transparent',
              color: 'var(--c-muted)',
              fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 600,
              fontSize: 13,
              padding: 11,
              borderRadius: 11,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            style={{
              flex: 1,
              border: 'none',
              background: 'var(--c-ink2)',
              color: 'var(--c-panel)',
              fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 600,
              fontSize: 13,
              padding: 11,
              borderRadius: 11,
              cursor: 'pointer',
            }}
          >
            Unlock
          </button>
        </div>
      </div>
    </div>
  );
}
