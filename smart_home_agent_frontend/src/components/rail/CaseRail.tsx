import type { CaseSummary, TrackerStep } from '../../types';
import ErrorBanner from './ErrorBanner';
import SummaryCard from './SummaryCard';
import Tracker from './Tracker';

interface Props {
  steps: TrackerStep[];
  solved: boolean;
  summary: CaseSummary;
  error: string;
  onNewCase: () => void;
  onOpenAdmin: () => void;
}

export default function CaseRail({ steps, solved, summary, error, onNewCase, onOpenAdmin }: Props) {
  return (
    <aside className="sh-rail sh-scroll flex flex-col">
      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '.5px',
            textTransform: 'uppercase',
            color: 'var(--c-faint)',
            marginBottom: 6,
          }}
        >
          Case file
        </div>
        <div
          style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 600,
            fontSize: 18,
            color: 'var(--c-ink)',
          }}
        >
          The Deduction
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--c-muted2)', marginTop: 3, fontWeight: 500 }}>
          OFFIS Smart Hotel Studio
        </div>
      </div>

      <Tracker steps={steps} />

      {solved && <SummaryCard summary={summary} onNewCase={onNewCase} />}

      {error && <ErrorBanner message={error} />}

      <button
        type="button"
        onClick={onOpenAdmin}
        className="flex items-center justify-center"
        style={{
          marginTop: 'auto',
          gap: 8,
          width: '100%',
          border: '1px solid var(--c-border-chip)',
          background: 'transparent',
          color: 'var(--c-muted)',
          fontFamily: "'Space Grotesk', sans-serif",
          fontWeight: 600,
          fontSize: 12.5,
          padding: '10px 12px',
          borderRadius: 11,
          cursor: 'pointer',
        }}
      >
        <span aria-hidden style={{ fontSize: 13 }}>🔒</span>
        Admin Panel
      </button>
    </aside>
  );
}
