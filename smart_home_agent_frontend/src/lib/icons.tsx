// Brand SVG icons, recreated 1:1 from the design prototype. The magnifying glass
// is the Sherlock.home brand motif and is intentionally kept hand-rolled.

interface IconProps {
  size?: number;
  className?: string;
}

/** Magnifying glass with a lens dot — used in the gold logo mark. */
export function LogoGlass({ size = 19, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <circle cx="10" cy="10" r="6.2" stroke="#2A2A30" strokeWidth="2.4" />
      <line x1="14.6" y1="14.6" x2="20" y2="20" stroke="#2A2A30" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="10" cy="10" r="2.4" fill="#FBFAF6" />
    </svg>
  );
}

/** Magnifying glass stroked in gold — used in the dark agent avatar. */
export function AvatarGlass({ size = 15, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <circle cx="10" cy="10" r="6.2" stroke="#E0992F" strokeWidth="2.4" />
      <line x1="14.6" y1="14.6" x2="20" y2="20" stroke="#E0992F" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

/** Location pin — floor-map card header. */
export function PinIcon({ size = 14, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11Z" stroke="#E0992F" strokeWidth="2" />
      <circle cx="12" cy="10" r="2.4" fill="#E0992F" />
    </svg>
  );
}

/** Paper-plane send glyph. */
export function SendIcon({ size = 19, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path d="M4 12 20 4l-4 16-4.5-6L4 12Z" fill="#2A2008" />
    </svg>
  );
}

/** Circular arrow — "start a new case" / reset. */
export function RefreshIcon({ size = 14, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M20 12a8 8 0 1 1-2.34-5.66"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <path
        d="M20 4v4.5h-4.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Check mark — case-closed badge. */
export function CheckIcon({ size = 15, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path d="m5 13 4 4 10-11" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
