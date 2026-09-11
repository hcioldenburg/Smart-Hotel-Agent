import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { SendIcon } from '../../lib/icons';

interface Props {
  disabled: boolean;
  onSend: (text: string) => void;
}

/** Imperative surface: the "Other…" quick-reply chip focuses the input via this. */
export interface InputBarHandle {
  focus: () => void;
}

const InputBar = forwardRef<InputBarHandle, Props>(function InputBar({ disabled, onSend }, ref) {
  const [value, setValue] = useState('');
  const [hover, setHover] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({ focus: () => textareaRef.current?.focus() }), []);

  // Auto-grow the textarea up to the 120px cap.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue('');
  };

  return (
    <div style={{ flex: 'none', padding: '16px var(--sh-pad-x) 22px' }}>
      <div
        className="flex"
        style={{
          maxWidth: 660,
          margin: '0 auto',
          alignItems: 'flex-end',
          gap: 10,
          background: 'var(--c-card)',
          border: '1px solid var(--c-border-input)',
          borderRadius: 16,
          padding: '8px 8px 8px 18px',
          boxShadow: '0 8px 24px rgba(40,38,32,.08)',
        }}
      >
        <textarea
          ref={textareaRef}
          className="sh-in"
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Describe what's misbehaving…"
          style={{
            flex: 1,
            minWidth: 0,
            border: 'none',
            outline: 'none',
            resize: 'none',
            background: 'transparent',
            fontSize: 14.5,
            lineHeight: 1.5,
            color: 'var(--c-ink)',
            padding: '9px 0',
            maxHeight: 120,
          }}
        />
        <button
          type="button"
          onClick={submit}
          aria-label="Send message"
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          className="flex items-center justify-center"
          style={{
            width: 42,
            height: 42,
            flex: 'none',
            border: 'none',
            borderRadius: 12,
            background: hover ? 'var(--c-gold-hover)' : 'var(--c-gold)',
            cursor: 'pointer',
            boxShadow: '0 4px 14px rgba(224,153,47,.4)',
          }}
        >
          <SendIcon />
        </button>
      </div>
      <div
        style={{
          maxWidth: 660,
          margin: '8px auto 0',
          textAlign: 'center',
          fontSize: 11,
          color: 'var(--c-faint)',
          fontWeight: 500,
        }}
      >
        Sherlock guides you through the SmartHotel portal — it diagnoses, you inspect.
      </div>
    </div>
  );
});

export default InputBar;
