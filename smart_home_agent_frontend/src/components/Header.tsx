import { useState } from 'react';
import { LogoGlass, RefreshIcon } from '../lib/icons';

interface Props {
  /** Status pill label: "Awaiting clues" | "On the case" | "Case closed". */
  statusLabel: string;
  /** Clears the conversation and starts a fresh case. */
  onNewCase: () => void;
  /** Opens the experimenter gate (PIN). Deliberately unlabelled and quiet. */
  onOpenAdmin: () => void;
}

export default function Header({ statusLabel, onNewCase, onOpenAdmin }: Props) {
  const [hover, setHover] = useState(false);
  return (
    <header
      className="flex items-center justify-between"
      style={{
        height: 66,
        flex: 'none',
        padding: '0 var(--sh-pad-header)',
        background: 'var(--c-panel)',
        borderBottom: '1px solid var(--c-border)',
      }}
    >
      <div className="flex items-center" style={{ gap: 12 }}>
        <div
          className="flex items-center justify-center"
          style={{
            width: 34,
            height: 34,
            borderRadius: 10,
            background: 'var(--c-gold)',
            boxShadow: '0 3px 12px rgba(224,153,47,.45)',
          }}
        >
          <LogoGlass />
        </div>
        <div className="flex flex-col" style={{ lineHeight: 1 }}>
          <span
            style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 700,
              fontSize: 20,
              letterSpacing: '-.5px',
              color: 'var(--c-ink)',
            }}
          >
            Sherlock<span style={{ color: 'var(--c-gold)' }}>.home</span>
          </span>
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--c-muted2)',
              fontWeight: 600,
              marginTop: 3,
              letterSpacing: '.2px',
            }}
          >
            Smart-home diagnostics
          </span>
        </div>
      </div>

      <div className="flex items-center" style={{ gap: 10 }}>
        <button
          type="button"
          onClick={onNewCase}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          title="Clear the conversation and start a fresh case"
          className="flex items-center"
          style={{
            gap: 7,
            border: '1px solid var(--c-border-chip)',
            background: hover ? 'var(--c-chat)' : 'transparent',
            color: hover ? 'var(--c-ink)' : 'var(--c-muted2)',
            borderRadius: 999,
            padding: '7px 13px',
            cursor: 'pointer',
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 600,
            fontSize: 12.5,
            letterSpacing: '.2px',
            transition: 'color .15s ease, background .15s ease',
          }}
        >
          <RefreshIcon />
          New case
        </button>

        {/* Experimenter entry. A quiet dot, not a labelled button: participants have no
            reason to press it, and the PIN gate stops them if they do. */}
        <button
          type="button"
          onClick={onOpenAdmin}
          title="Experimenter"
          aria-label="Experimenter panel"
          style={{
            width: 26,
            height: 26,
            borderRadius: 999,
            border: '1px solid var(--c-border-chip)',
            background: 'transparent',
            color: 'var(--c-muted2)',
            cursor: 'pointer',
            fontSize: 12,
            lineHeight: 1,
          }}
        >
          •
        </button>

        <div
          className="flex items-center"
          style={{
            gap: 8,
            border: '1px solid var(--c-border-chip)',
            background: 'var(--c-chat)',
            borderRadius: 999,
            padding: '7px 14px',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--c-green)',
              boxShadow: '0 0 0 3px rgba(91,174,122,.18)',
            }}
          />
          <span
            style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 600,
              fontSize: 12.5,
              color: 'var(--c-body)',
              letterSpacing: '.2px',
            }}
          >
            {statusLabel}
          </span>
        </div>
      </div>
    </header>
  );
}
