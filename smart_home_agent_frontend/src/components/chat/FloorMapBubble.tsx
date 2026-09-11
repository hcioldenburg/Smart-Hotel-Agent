import { PinIcon } from '../../lib/icons';
import type { FloorMapView } from '../../types';

interface Props {
  map: FloorMapView;
}

export default function FloorMapBubble({ map }: Props) {
  return (
    <div
      style={{
        background: 'var(--c-card)',
        border: '1px solid var(--c-border-card)',
        borderRadius: 14,
        overflow: 'hidden',
        boxShadow: '0 8px 24px rgba(40,38,32,.10)',
        maxWidth: 420,
      }}
    >
      <div
        className="flex items-center"
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--c-border-soft)',
          gap: 8,
        }}
      >
        <PinIcon />
        <span
          style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 600,
            fontSize: 13,
            color: 'var(--c-body2)',
          }}
        >
          {map.deviceName}
        </span>
      </div>

      <img
        src={map.src}
        alt={`Floor map highlighting ${map.deviceName}`}
        onError={(e) => {
          const img = e.currentTarget;
          // Fall back to the bundled local plan once, if the backend src failed.
          if (map.fallbackSrc && img.src !== map.fallbackSrc) {
            img.src = map.fallbackSrc;
          }
        }}
        style={{ display: 'block', width: '100%', background: 'var(--c-panel)' }}
      />

      {map.caption && (
        <div
          style={{
            padding: '10px 14px',
            fontSize: 12.5,
            color: 'var(--c-muted)',
            lineHeight: 1.45,
          }}
        >
          {map.caption}
        </div>
      )}
    </div>
  );
}
