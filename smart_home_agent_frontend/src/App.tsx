import { useCallback, useMemo, useRef, useState } from 'react';
import AdminGate from './components/admin/AdminGate';
import AdminPanel from './components/admin/AdminPanel';
import ConditionGate, { type PresentationMode } from './components/ConditionGate';
import Header from './components/Header';
import InputBar, { type InputBarHandle } from './components/chat/InputBar';
import MessageList from './components/chat/MessageList';
import QuickReplies from './components/chat/QuickReplies';
import CaseRail from './components/rail/CaseRail';
import { useChat } from './hooks/useChat';
import { buildSteps, buildSummary, buildTracker, caseLabel } from './lib/diagnosis';

export default function App() {
  // Study condition AND scenario: both null until the experimenter picks them at case start.
  // The ConditionGate blocks the chat until then, and reopens on "Start a new case".
  //
  // The scenario is a GRADING LABEL only. Without it the session logs scenario_id: null and can
  // never be scored against ground truth — silently. The agent itself never sees the scenario:
  // it is a general troubleshooter for the whole lab and works whatever the participant brings.
  const [presentationMode, setPresentationMode] = useState<PresentationMode | null>(null);
  const [scenarioId, setScenarioId] = useState<string | null>(null);

  const { messages, latest, starters, options, typing, error, send, reset } = useChat(
    presentationMode,
    scenarioId ?? undefined,
  );

  // "Other…" on the answer buttons doesn't send anything — it hands the
  // participant to the free-text input, so the buttons never feel like a cage.
  const inputRef = useRef<InputBarHandle>(null);

  // Admin history: 'gate' asks for the PIN, 'open' shows the history drawer.
  const [admin, setAdmin] = useState<'closed' | 'gate' | 'open'>('closed');

  // The case rail (working hypothesis, deduction steps, case summary) is EXPERIMENTER
  // instrumentation. Shown to a participant it narrates the agent's diagnosis at them —
  // which both solves the case for them and contaminates the study measure. It appears
  // only after the admin PIN has been entered once in this browser session.
  const [railUnlocked, setRailUnlocked] = useState(false);

  // New case: wipe the conversation and reopen the condition gate.
  const newCase = useCallback(() => {
    void reset();
    setPresentationMode(null);
    setScenarioId(null);
  }, [reset]);

  const tracker = useMemo(() => buildTracker(latest), [latest]);
  const steps = useMemo(() => buildSteps(tracker), [tracker]);
  const summary = useMemo(() => buildSummary(tracker), [tracker]);

  const solved = tracker.stage === 'solved';
  const statusLabel = caseLabel(tracker.stage, solved);
  const showQuick = starters.length > 0 && !solved && !typing;
  // Answer buttons for the agent's current check (e.g. Open / Closed /
  // Unavailable) — only when the backend says the answer has a closed set.
  const showOptions = !showQuick && options.length > 0 && !solved && !typing;

  return (
    <div className="sh-shell flex items-center justify-center">
      <div className="sh-panel flex flex-col">
        <Header statusLabel={statusLabel} onNewCase={newCase} onOpenAdmin={() => setAdmin('gate')} />

        <div className="sh-body flex">
          {/* Chat column */}
          <div className="sh-chatcol flex flex-col">
            <MessageList messages={messages} typing={typing} />
            {showQuick && <QuickReplies replies={starters} onSelect={send} />}
            {showOptions && (
              <QuickReplies
                replies={options}
                onSelect={send}
                onOther={() => inputRef.current?.focus()}
              />
            )}
            <InputBar ref={inputRef} disabled={typing} onSend={send} />
          </div>

          {/* Case rail — experimenter-only; see railUnlocked above. */}
          {railUnlocked && (
            <CaseRail
              steps={steps}
              solved={solved}
              summary={summary}
              error={error}
              onNewCase={newCase}
              onOpenAdmin={() => setAdmin('gate')}
            />
          )}
        </div>

        {(!presentationMode || !scenarioId) && (
          <ConditionGate
            onChoose={(mode, scenario) => {
              setPresentationMode(mode);
              setScenarioId(scenario);
            }}
          />
        )}

        {admin === 'gate' && (
          <AdminGate
            onUnlock={() => {
              setAdmin('open');
              setRailUnlocked(true);
            }}
            onCancel={() => setAdmin('closed')}
          />
        )}
        {admin === 'open' && <AdminPanel onClose={() => setAdmin('closed')} />}
      </div>
    </div>
  );
}
