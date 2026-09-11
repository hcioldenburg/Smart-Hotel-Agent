// Study gate. Shown to the experimenter at the start of every case (before any chat), it
// captures the two things only they know: which portal build the participant is on, and which
// scenario is set up in the room. Re-shown on "Start a new case".
//
// WHAT THE SCENARIO CHOICE IS — AND IS NOT:
//
//   It IS a grading label. The session is stamped with the scenario_id so it can be scored
//   against configs/ground_truth/<id>.yaml afterwards. Every pilot session logged
//   scenario_id: null, and as a result not one of them can ever be graded — nothing on record
//   says what the right answer was. That is why this field is required.
//
//   It is NOT sent to the agent's reasoning. The agent is a GENERAL troubleshooter for the
//   whole lab: it knows the devices, both portal builds, the rules, the switches and the time
//   model, and it works whatever problem the participant brings — never primed with which
//   predefined case, if any, is running. (config_loader deliberately does not render the
//   scenario into the prompt; do not "fix" that.)
//
// Neither half errors when missing — the session just becomes silently ungradable. So the gate
// refuses to open the chat until both are chosen.

import { useState } from 'react';

export type PresentationMode = 'dashboard' | 'floor_map';

// Must match configs/reasoning-model_scenarios/<id>.yaml, which must in turn match
// configs/ground_truth/<id>.yaml. scripts/check_prompt_leakage.py keeps those in step.
// Labels are the study's vignette letters, in vignette order.
export const SCENARIOS: { id: string; label: string }[] = [
  { id: 'SC-WINDOW-OPEN-SHUTTER-OPEN', label: 'Vignette A' },
  { id: 'SC-MOVIE-MODE-TV-ON-FAN-OFF', label: 'Vignette B' },
  { id: 'SC-EVENING-WIND-DOWN', label: 'Vignette C' },
  { id: 'SC-MORNING-ROUTINE', label: 'Vignette D' },
  { id: 'SC-DOOR-WINDOW-OPEN-FAN-OFF', label: 'Vignette E' },
  { id: 'SC-TV-ON-BEDLIGHT-AMBIENT', label: 'Vignette F' },
  { id: 'SC-WINDOW-CLOSED-FAN-ON', label: 'Vignette G' },
  { id: 'SC-ENTRANCE-DOOR-LIGHT-ON', label: 'Vignette H' },
  { id: 'SC-TEMP-LOW-HEATER-ON', label: 'Vignette I' },
];

interface Props {
  onChoose: (mode: PresentationMode, scenarioId: string) => void;
}

const OPTIONS: { mode: PresentationMode; title: string; blurb: string }[] = [
  { mode: 'dashboard', title: 'Dashboard', blurb: 'Your Room is grouped device cards (port 5172).' },
  { mode: 'floor_map', title: 'Floor Map', blurb: 'Your Room is a spatial floor map with a right-hand panel (port 5173).' },
];

export default function ConditionGate({ onChoose }: Props) {
  const [mode, setMode] = useState<PresentationMode | null>(null);
  const [scenario, setScenario] = useState<string>('');

  const ready = mode !== null && scenario !== '';

  return (
    <div
      className="flex items-center justify-center"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 50,
        background: 'rgba(37,36,42,.42)',
        backdropFilter: 'blur(2px)',
        animation: 'shFade .18s ease both',
      }}
    >
      <div
        style={{
          width: 460,
          maxWidth: 'calc(100% - 40px)',
          background: 'var(--c-panel)',
          border: '1px solid var(--c-border)',
          borderRadius: 18,
          padding: 24,
          boxShadow: '0 24px 60px rgba(40,38,32,.32)',
          animation: 'shPop .22s ease both',
        }}
      >
        <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 17, color: 'var(--c-ink)' }}>
          Start a case
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--c-muted2)', marginTop: 4, fontWeight: 500, lineHeight: 1.4 }}>
          Both are required. The scenario only labels the session for grading — the agent never
          sees it, and works whatever problem the participant brings.
        </div>

        {/* 1 — portal build */}
        <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--c-muted2)', marginTop: 18, letterSpacing: '.04em' }}>
          1&nbsp;&nbsp;PORTAL BUILD
        </div>
        <div className="flex" style={{ gap: 12, marginTop: 8 }}>
          {OPTIONS.map((o) => (
            <button
              key={o.mode}
              type="button"
              onClick={() => setMode(o.mode)}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                textAlign: 'left',
                border: `1px solid ${mode === o.mode ? 'var(--c-ink2)' : 'var(--c-border-input)'}`,
                boxShadow: mode === o.mode ? 'inset 0 0 0 1px var(--c-ink2)' : 'none',
                background: 'var(--c-chat)',
                color: 'var(--c-ink)',
                padding: '14px 14px 16px',
                borderRadius: 14,
                cursor: 'pointer',
                transition: 'all .15s',
              }}
            >
              <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 15 }}>
                {o.title}
              </span>
              <span style={{ fontSize: 12, color: 'var(--c-muted2)', fontWeight: 500, lineHeight: 1.35 }}>
                {o.blurb}
              </span>
            </button>
          ))}
        </div>

        {/* 2 — scenario */}
        <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--c-muted2)', marginTop: 18, letterSpacing: '.04em' }}>
          2&nbsp;&nbsp;SCENARIO IN THE ROOM
        </div>
        <select
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          style={{
            width: '100%',
            marginTop: 8,
            padding: '11px 12px',
            borderRadius: 12,
            border: '1px solid var(--c-border-input)',
            background: 'var(--c-chat)',
            color: 'var(--c-ink)',
            fontSize: 13,
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          <option value="">— select the scenario you have set up —</option>
          {SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>

        <button
          type="button"
          disabled={!ready}
          onClick={() => ready && onChoose(mode as PresentationMode, scenario)}
          style={{
            width: '100%',
            marginTop: 18,
            padding: '12px 14px',
            borderRadius: 12,
            border: 'none',
            background: ready ? 'var(--c-ink)' : 'var(--c-border-input)',
            color: ready ? 'var(--c-panel)' : 'var(--c-muted2)',
            fontFamily: "'Space Grotesk', sans-serif",
            fontWeight: 700,
            fontSize: 14,
            cursor: ready ? 'pointer' : 'not-allowed',
            transition: 'all .15s',
          }}
        >
          {ready ? 'Begin session' : 'Choose a build and a scenario'}
        </button>
      </div>
    </div>
  );
}
