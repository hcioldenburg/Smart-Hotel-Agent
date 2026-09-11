import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types';
import AgentMessage from './AgentMessage';
import TypingIndicator from './TypingIndicator';
import UserMessage from './UserMessage';

interface Props {
  messages: ChatMessage[];
  typing: boolean;
}

export default function MessageList({ messages, typing }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom after every change (scrollTop, not scrollIntoView).
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, typing]);

  return (
    <div
      ref={scrollRef}
      className="sh-scroll"
      style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '26px var(--sh-pad-x) 12px' }}
    >
      <div
        className="flex flex-col"
        style={{ maxWidth: 660, margin: '0 auto', gap: 18 }}
      >
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <UserMessage key={i} text={m.content} />
          ) : (
            <AgentMessage key={i} text={m.content} floorMaps={m.floorMaps} />
          ),
        )}

        {typing && <TypingIndicator />}
      </div>
    </div>
  );
}
