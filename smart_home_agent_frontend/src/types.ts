// Types mirroring the smart_home_agent_backend Pydantic models, plus the
// front-end view models the Sherlock.home UI renders.

export interface VisualOutput {
  path?: string;
  /** Inline base64 image — prefer this for <img src>. */
  data_url?: string | null;
  exists?: boolean;
  /** Device this image depicts — the authoritative key for its header. */
  related_device_id?: string | null;
  caption?: string | null;
  [k: string]: unknown;
}

/** One investigative move in the case-file timeline (from the backend). */
export interface DeductionStep {
  /** The hypothesis this move tests, e.g. "Automation / rule". */
  suspect: string;
  /** What the agent asked the participant to do. */
  action: string;
  /** What the participant reported back ("" while still open). */
  result: string;
  action_type?: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;

  /** Which portal build the session runs in: "dashboard" | "floor_map". */
  presentation_mode?: string | null;

  diagnosis_phase?: string | null;
  next_action_type?: string | null;

  reported_symptom?: string | null;
  /** Greeting-free symptom label for the case file. */
  normalized_symptom?: string | null;
  target_device?: string | null;
  target_device_id?: string | null;
  target_device_type?: string | null;

  /** The participant's stated suspect (their mind-map), not the probed device. */
  primary_suspect_label?: string | null;
  primary_suspect_type?: string | null;
  suspect_source?: string | null;

  last_check_requested?: string | null;
  pending_question?: string | null;
  issue_identified: boolean;
  /**
   * Expected answers to the check the agent just requested (e.g. "Open" /
   * "Closed" / "Unavailable"), rendered as quick-reply buttons. Empty for
   * open-ended questions. The UI appends its own "Other…" free-text chip.
   */
  reply_options?: string[];
  /** Concluded cause, once found. */
  root_cause?: string | null;
  recommended_action?: string | null;

  /** Ordered suspect → action → result timeline. */
  deduction_timeline?: DeductionStep[];

  visual_outputs: VisualOutput[];
  portal_context: Record<string, unknown>;
  evidence: Array<Record<string, unknown>>;
  checked_tools: Array<Record<string, unknown>>;
  checked_devices: string[];
}

export interface SessionMessage {
  role: string;
  content: string;
  tool_calls?: unknown;
}

export interface SessionStateResponse extends Omit<ChatResponse, 'reply'> {
  messages: SessionMessage[];
}

export interface SessionListItem {
  session_id: string;
  created_at: string;
  updated_at: string;
  reported_symptom?: string | null;
  diagnosis_phase?: string | null;
  issue_identified: boolean;
  observed_strategy?: string | null;
  scenario_id?: string | null;
}

// ─── Front-end view models ───

/** A floor-map card to render inside an agent turn. */
export interface FloorMapView {
  src: string;
  /** Reliable fallback (bundled local plan) if `src` fails to load. */
  fallbackSrc?: string;
  deviceName: string;
  caption: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  /** Structured fields from the agent, attached to assistant turns. */
  meta?: ChatResponse;
  /** Floor-map cards derived from the turn's visual_outputs. */
  floorMaps?: FloorMapView[];
}

/** Diagnosis lifecycle, driving the case tracker + status pill. */
export type Stage =
  | 'idle'
  | 'symptom'
  | 'suspect'
  | 'investigating'
  | 'cause'
  | 'solved';

export interface Tracker {
  stage: Stage;
  symptom: string;
  suspect: string;
  cause: string;
  /** Ordered investigative steps between suspect and root cause. */
  steps: DeductionStep[];
}

/** A labelled sub-line under a tracker step (e.g. Action / Result). */
export interface TrackerLine {
  label: string;
  text: string;
  /** Muted italic placeholder styling (e.g. "Awaiting your reply…"). */
  pending?: boolean;
}

export interface TrackerStep {
  label: string;
  detail: string;
  /** True when the captured text is filled (vs. placeholder). */
  filled: boolean;
  active: boolean;
  done: boolean;
  /** Pulse the gold ring (currently-active, not-yet-done step). */
  pulse: boolean;
  showLine: boolean;
  /** Optional Action/Result sub-lines for an investigation step. */
  lines?: TrackerLine[];
}

export interface CaseSummary {
  symptom: string;
  suspect: string;
  cause: string;
  note: string;
}
