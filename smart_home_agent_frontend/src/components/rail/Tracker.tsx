import { CheckIcon } from '../../lib/icons';
import type { TrackerStep } from '../../types';

interface Props {
  steps: TrackerStep[];
}

function StepRow({ step }: { step: TrackerStep }) {
  const dotBackground = step.active ? 'var(--c-gold)' : 'var(--c-dot-pending)';
  return (
    <div className="flex" style={{ gap: 13, paddingBottom: 18, position: 'relative' }}>
      <div className="flex flex-col items-center" style={{ flex: 'none' }}>
        <div
          className="flex items-center justify-center"
          style={{
            width: 22,
            height: 22,
            borderRadius: '50%',
            background: dotBackground,
            boxShadow: step.active ? '0 0 0 4px rgba(224,153,47,.16)' : 'none',
            animation: step.pulse ? 'shPulse 1.8s infinite' : undefined,
            flex: 'none',
          }}
        >
          {step.done && <CheckIcon size={13} />}
        </div>
        {step.showLine && (
          <div
            style={{
              width: 2,
              flex: 1,
              minHeight: 26,
              marginTop: 4,
              background: step.active ? 'var(--c-line-active)' : 'var(--c-line-idle)',
            }}
          />
        )}
      </div>

      <div style={{ minWidth: 0, paddingTop: 1 }}>
        <div
          style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 600,
            fontSize: 13.5,
            color: 'var(--c-body2)',
          }}
        >
          {step.label}
        </div>

        {step.lines && step.lines.length > 0 ? (
          <div className="flex flex-col" style={{ gap: 3, marginTop: 4 }}>
            {step.lines.map((line, i) => (
              <div key={i} style={{ fontSize: 12, lineHeight: 1.4 }}>
                <span style={{ fontWeight: 600, color: 'var(--c-muted)' }}>{line.label}: </span>
                <span
                  style={{
                    color: line.pending ? 'var(--c-faint2)' : 'var(--c-muted)',
                    fontStyle: line.pending ? 'italic' : 'normal',
                  }}
                >
                  {line.text}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              fontSize: 12.5,
              lineHeight: 1.4,
              marginTop: 3,
              color: step.filled ? 'var(--c-muted)' : 'var(--c-faint2)',
              fontStyle: step.filled ? 'normal' : 'italic',
            }}
          >
            {step.detail}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Tracker({ steps }: Props) {
  return (
    <div
      style={{
        background: 'var(--c-card)',
        border: '1px solid var(--c-border-card)',
        borderRadius: 16,
        padding: '18px 18px 6px',
        boxShadow: '0 6px 18px rgba(40,38,32,.06)',
      }}
    >
      {steps.map((step, i) => (
        <StepRow key={`${i}-${step.label}`} step={step} />
      ))}
    </div>
  );
}
