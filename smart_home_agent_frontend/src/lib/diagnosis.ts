// Adapts the backend ChatResponse into the design's diagnosis view model
// (tracker, status pill, summary, floor-map bubbles). The prototype used an
// inline @@{...} state line; the real backend exposes the fields directly, so
// here we map them and degrade gracefully when a field isn't provided.

import { backendAssetUrl, SUMMARY_NOTE } from '../config';
import { DEVICES, localFloorplanUrl } from '../data/devices';
import type {
  CaseSummary,
  ChatResponse,
  FloorMapView,
  Stage,
  Tracker,
  TrackerStep,
  VisualOutput,
} from '../types';

/** Strip stray Markdown so plain chat bubbles stay clean. */
export function cleanText(input: string): string {
  return input
    .replace(/\[\[[^\]]*\]\]/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`+/g, '')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const STAGE_LEVEL: Record<Stage, number> = {
  idle: 0,
  symptom: 1,
  suspect: 2,
  investigating: 2,
  cause: 3,
  solved: 3,
};

/** Best-effort map of the backend's free-form diagnosis_phase → a UI Stage. */
function mapStage(res: ChatResponse): Stage {
  if (res.issue_identified) return 'solved';
  const phase = (res.diagnosis_phase ?? '').toLowerCase();
  if (phase.includes('solv') || phase.includes('conclud') || phase.includes('done')) return 'solved';
  if (phase.includes('cause') || phase.includes('root')) return 'cause';
  if (phase.includes('investigat') || phase.includes('check') || phase.includes('inspect')) return 'investigating';
  if (phase.includes('suspect') || phase.includes('hypoth')) return 'suspect';
  if (phase.includes('symptom') || phase.includes('intake') || phase.includes('report')) return 'symptom';
  if (res.target_device || res.target_device_type) return 'suspect';
  if (res.reported_symptom) return 'symptom';
  return 'idle';
}

function readString(obj: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const v = obj[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return '';
}

/** Build the running case tracker from the latest agent response. */
export function buildTracker(res: ChatResponse | null): Tracker {
  if (!res) return { stage: 'idle', symptom: '', suspect: '', cause: '', steps: [] };

  // Prefer the greeting-free symptom the backend now derives.
  const symptom = (res.normalized_symptom || res.reported_symptom || '').trim();

  const steps = (res.deduction_timeline ?? []).filter(
    (s): s is NonNullable<typeof s> => !!s && !!(s.suspect || s.action || s.result),
  );

  // Prime suspect = the participant's own stated suspect (their mind-map). When
  // they named none, fall back to the first hypothesis the investigation tested
  // — never the device currently being probed, which was the old bug.
  const suspect = (res.primary_suspect_label || '').trim() || (steps[0]?.suspect ?? '').trim();

  // root_cause is now a first-class backend field; keep the older fallbacks so
  // an in-flight or older session still shows something sensible.
  const cause =
    cleanText((res.root_cause || '').trim()) ||
    readString(res.portal_context ?? {}, ['root_cause', 'cause', 'conclusion']) ||
    (res.issue_identified ? cleanText(res.reply) : '');

  return { stage: mapStage(res), symptom, suspect, cause, steps };
}

/**
 * Case-file timeline: Symptom → (suspect / action / result)* → Root cause.
 * The middle rows come straight from the backend's deduction_timeline; the
 * first/last rows frame it.
 */
export function buildSteps(tracker: Tracker): TrackerStep[] {
  const level = STAGE_LEVEL[tracker.stage] ?? 0;
  const solved = tracker.stage === 'solved';
  const rows: TrackerStep[] = [];

  // 1. Symptom
  const symptomFilled = !!tracker.symptom.trim();
  const investigationStarted = tracker.steps.length > 0;
  rows.push({
    label: 'Symptom',
    detail: symptomFilled ? tracker.symptom : 'Describe the problem',
    filled: symptomFilled,
    active: level >= 1 || symptomFilled,
    done: symptomFilled && (investigationStarted || level >= 2 || solved),
    pulse: symptomFilled && !investigationStarted && level < 2 && !solved,
    showLine: true,
  });

  // 2. Investigation timeline — one row per suspect, with Action / Result.
  tracker.steps.forEach((step, i) => {
    const action = (step.action || '').trim();
    const result = (step.result || '').trim();
    const closed = !!result;
    const isLast = i === tracker.steps.length - 1;

    const lines: TrackerStep['lines'] = [];
    if (action) lines.push({ label: 'Action', text: action });
    lines.push(
      closed
        ? { label: 'Result', text: result }
        : { label: 'Result', text: 'Awaiting your reply…', pending: true },
    );

    rows.push({
      label: (step.suspect || '').trim() || `Suspect ${i + 1}`,
      detail: '',
      filled: true,
      active: true,
      done: closed && (solved || !isLast),
      pulse: !closed && !solved,
      showLine: true,
      lines,
    });
  });

  // 3. Root cause
  const causeFilled = !!tracker.cause.trim();
  rows.push({
    label: 'Root cause',
    detail: causeFilled ? tracker.cause : 'To be revealed',
    filled: causeFilled,
    active: level >= 3 || causeFilled,
    done: solved || causeFilled,
    pulse: false,
    showLine: false,
  });

  return rows;
}

export function buildSummary(tracker: Tracker): CaseSummary {
  return {
    symptom: tracker.symptom || '—',
    suspect: tracker.suspect || '—',
    cause: tracker.cause || '—',
    note: SUMMARY_NOTE,
  };
}

export function caseLabel(stage: Stage, solved: boolean): string {
  if (solved) return 'Case closed';
  return stage === 'idle' ? 'Awaiting clues' : 'On the case';
}

/**
 * Resolve a backend image to a usable <img src>. We prefer an inline data_url
 * (works everywhere, including the iPad), then the bundled local plan keyed by
 * device id (reliable, identical asset), then the backend's site-relative path.
 */
function visualSrc(visual: { data_url?: string | null; path?: string }, deviceId?: string): string {
  if (visual.data_url) return visual.data_url;
  if (deviceId) return localFloorplanUrl(deviceId);
  if (visual.path) return backendAssetUrl(visual.path);
  return '';
}

/** Guess a device id from a visual's path (e.g. /assets/floorplans/smartfan.png). */
function deviceIdFromPath(path?: string): string | undefined {
  if (!path) return undefined;
  const base = path.split(/[\\/]/).pop() ?? '';
  const id = base.replace(/\.[a-z0-9]+$/i, '');
  return DEVICES[id] ? id : undefined;
}

/**
 * Turn visuals into floor-map cards for the chat. Each card's device is resolved
 * from that visual's own `related_device_id`/path — not the turn's single
 * `target_device` — so a turn with several images (the backend accumulates
 * visual_outputs across the session) labels each one correctly.
 *
 * Pass `visuals` to render only a subset (e.g. the visuals new to this turn);
 * defaults to the whole response.
 */
export function buildFloorMaps(
  res: ChatResponse,
  visuals: VisualOutput[] = res.visual_outputs ?? [],
): FloorMapView[] {
  return visuals
    .map((visual): FloorMapView | null => {
      const relatedId =
        typeof visual.related_device_id === 'string' && DEVICES[visual.related_device_id]
          ? visual.related_device_id
          : undefined;
      const id =
        relatedId ||
        deviceIdFromPath(visual.path) ||
        (res.target_device_id && DEVICES[res.target_device_id] ? res.target_device_id : undefined);
      const info = id ? DEVICES[id] : undefined;
      const src = visualSrc(visual, id);
      if (!src) return null;
      return {
        src,
        fallbackSrc: id ? localFloorplanUrl(id) : undefined,
        deviceName: info?.name || res.target_device || 'Device location',
        // Location hint is already stated in the agent's chat reply; omit it here
        // to avoid duplicating the text under the image.
        caption: '',
      };
    })
    .filter((v): v is FloorMapView => v !== null);
}
