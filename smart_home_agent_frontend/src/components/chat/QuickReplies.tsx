import { useState } from 'react';

interface Props {
  replies: string[];
  onSelect: (text: string) => void;
  /**
   * When set, appends an "Other…" chip after the replies. Answer buttons are a
   * hint at what the reading should look like, never a cage: "Other…" hands the
   * participant back to free text (focuses the input) instead of sending anything.
   */
  onOther?: () => void;
}

function Chip({
  text,
  onClick,
  muted = false,
}: {
  text: string;
  onClick: () => void;
  /** Dashed, quieter styling for the escape-hatch "Other…" chip. */
  muted?: boolean;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `1px ${muted ? 'dashed' : 'solid'} ${hover ? '#E7C98A' : 'var(--c-border-chip)'}`,
        background: hover ? 'var(--c-accent-panel)' : muted ? 'transparent' : 'var(--c-card)',
        color: muted ? 'var(--c-faint)' : 'var(--c-chip-text)',
        fontFamily: "'Manrope', sans-serif",
        fontWeight: 600,
        fontSize: 13,
        padding: '8px 14px',
        borderRadius: 999,
        cursor: 'pointer',
        boxShadow: muted ? 'none' : '0 2px 8px rgba(40,38,32,.05)',
      }}
    >
      {text}
    </button>
  );
}

export default function QuickReplies({ replies, onSelect, onOther }: Props) {
  if (replies.length === 0) return null;
  return (
    <div style={{ flex: 'none', padding: '4px var(--sh-pad-x) 0' }}>
      <div
        className="flex flex-wrap"
        style={{ maxWidth: 660, margin: '0 auto', gap: 8 }}
      >
        {replies.map((text) => (
          <Chip key={text} text={text} onClick={() => onSelect(text)} />
        ))}
        {onOther && <Chip text="Other…" onClick={onOther} muted />}
      </div>
    </div>
  );
}
