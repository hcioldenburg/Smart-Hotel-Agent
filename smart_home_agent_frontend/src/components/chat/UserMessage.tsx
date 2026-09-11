interface Props {
  text: string;
}

export default function UserMessage({ text }: Props) {
  return (
    <div className="flex" style={{ justifyContent: 'flex-end' }}>
      <div
        style={{
          maxWidth: '78%',
          background: 'var(--c-gold)',
          color: 'var(--c-on-gold)',
          fontSize: 14.5,
          lineHeight: 1.5,
          fontWeight: 500,
          padding: '11px 16px',
          borderRadius: '16px 16px 4px 16px',
          boxShadow: '0 4px 14px rgba(224,153,47,.28)',
          whiteSpace: 'pre-wrap',
        }}
      >
        {text}
      </div>
    </div>
  );
}
