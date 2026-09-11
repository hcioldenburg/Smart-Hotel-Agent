interface Props {
  message: string;
}

export default function ErrorBanner({ message }: Props) {
  return (
    <div
      role="alert"
      style={{
        background: 'var(--c-err-bg)',
        border: '1px solid var(--c-err-brd)',
        borderRadius: 12,
        padding: '12px 14px',
        fontSize: 12.5,
        color: 'var(--c-err-text)',
        lineHeight: 1.5,
      }}
    >
      {message}
    </div>
  );
}
