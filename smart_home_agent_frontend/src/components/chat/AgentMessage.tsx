import type { FloorMapView } from '../../types';
import AgentAvatar from './AgentAvatar';
import FloorMapBubble from './FloorMapBubble';

interface Props {
  text: string;
  floorMaps?: FloorMapView[];
}

export default function AgentMessage({ text, floorMaps }: Props) {
  return (
    <div className="flex" style={{ gap: 11, alignItems: 'flex-start' }}>
      <AgentAvatar offset />
      <div className="flex flex-col" style={{ minWidth: 0, gap: 10 }}>
        {text && (
          <div
            style={{
              maxWidth: '100%',
              background: 'var(--c-card)',
              border: '1px solid var(--c-border-card)',
              color: 'var(--c-body)',
              fontSize: 14.5,
              lineHeight: 1.55,
              padding: '12px 16px',
              borderRadius: '4px 16px 16px 16px',
              boxShadow: '0 6px 18px rgba(40,38,32,.07)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {text}
          </div>
        )}

        {floorMaps?.map((map, i) => (
          <FloorMapBubble key={i} map={map} />
        ))}
      </div>
    </div>
  );
}
