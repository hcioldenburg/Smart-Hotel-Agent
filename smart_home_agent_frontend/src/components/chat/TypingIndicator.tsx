import AgentAvatar from './AgentAvatar';

function Dot({ delay }: { delay: string }) {
  return (
    <span
      style={{
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: 'var(--c-gold-soft)',
        animation: `shBlink 1.2s infinite both ${delay}`,
      }}
    />
  );
}

export default function TypingIndicator() {
  return (
    <div className="flex" style={{ gap: 11, alignItems: 'flex-start' }}>
      <AgentAvatar />
      <div
        className="flex items-center"
        style={{
          background: 'var(--c-card)',
          border: '1px solid var(--c-border-card)',
          padding: '14px 18px',
          borderRadius: '4px 16px 16px 16px',
          boxShadow: '0 6px 18px rgba(40,38,32,.07)',
          gap: 5,
        }}
      >
        <Dot delay="0s" />
        <Dot delay=".2s" />
        <Dot delay=".4s" />
      </div>
    </div>
  );
}
