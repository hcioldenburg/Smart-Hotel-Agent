import { AvatarGlass } from '../../lib/icons';

interface Props {
  /** Nudge down 2px when paired with a text bubble (matches the design). */
  offset?: boolean;
}

export default function AgentAvatar({ offset = false }: Props) {
  return (
    <div
      className="flex items-center justify-center"
      style={{
        width: 30,
        height: 30,
        flex: 'none',
        borderRadius: 9,
        background: 'var(--c-ink2)',
        marginTop: offset ? 2 : 0,
      }}
    >
      <AvatarGlass />
    </div>
  );
}
