import { useState } from 'react';
import { CheckIcon } from '../../lib/icons';
import type { CaseSummary } from '../../types';

interface Props {
  summary: CaseSummary;
  onNewCase: () => void;
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          letterSpacing: '.4px',
          textTransform: 'uppercase',
          color: 'var(--c-gold-label)',
        }}
      >
        {label}
      </div>
      <div style={{ fontSize: 13, color: 'var(--c-body)', lineHeight: 1.45, marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}

export default function SummaryCard({ summary, onNewCase }: Props) {
  const [hover, setHover] = useState(false);
  return (
    <div
      style={{
        background: 'var(--c-accent-panel)',
        border: '1px solid var(--c-accent-brd)',
        borderRadius: 16,
        padding: 18,
        boxShadow: '0 6px 18px rgba(40,38,32,.06)',
        animation: 'shPop .3s ease both',
      }}
    >
      <div className="flex items-center" style={{ gap: 9, marginBottom: 14 }}>
        <span
          className="flex items-center justify-center"
          style={{
            width: 26,
            height: 26,
            borderRadius: 8,
            background: 'var(--c-green)',
            boxShadow: '0 2px 8px rgba(91,174,122,.4)',
          }}
        >
          <CheckIcon />
        </span>
        <span
          style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 700,
            fontSize: 15,
            color: 'var(--c-ink)',
          }}
        >
          Case closed
        </span>
      </div>

      <div className="flex flex-col" style={{ gap: 11 }}>
        <Field label="Symptom" value={summary.symptom} />
        <Field label="Prime suspect" value={summary.suspect} />
        <Field label="Root cause" value={summary.cause} />
      </div>

      <div
        style={{
          marginTop: 14,
          paddingTop: 13,
          borderTop: '1px solid var(--c-accent-brd)',
          fontSize: 12,
          color: 'var(--c-muted)',
          lineHeight: 1.5,
        }}
      >
        {summary.note}
      </div>

      <button
        type="button"
        onClick={onNewCase}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          marginTop: 14,
          width: '100%',
          border: 'none',
          background: hover ? '#3A3A42' : 'var(--c-ink2)',
          color: 'var(--c-panel)',
          fontFamily: "'Space Grotesk', sans-serif",
          fontWeight: 600,
          fontSize: 13,
          padding: 11,
          borderRadius: 11,
          cursor: 'pointer',
        }}
      >
        Start a new case
      </button>
    </div>
  );
}
