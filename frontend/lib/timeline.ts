import { Effects, StoryEntry, TimelineRow } from "./api";

/**
 * Sequence-time layout of the timeline. Mirrors backend/core/render_graph.py
 * (speed changes, 0.5s crossfades that overlap neighbouring clips) so the
 * preview player, playhead, text overlays and music line up with the render.
 */
export const TRANSITION_MS = 500;
export const TRANSITIONS = ["cut", "dissolve", "cross_fade", "fade_to_black", "wipe", "zoom_in", "zoom_out"];
export const FILTERS = ["none", "bw", "warm", "cool", "vivid", "vintage", "cinematic"];
export const ROLES = ["hook", "context", "rising_action", "climax", "reaction", "resolution", "broll", "title_card"];

export interface LaidOut {
  entry: StoryEntry;
  index: number;
  srcIn: number; // source-clip ms
  srcOut: number;
  speed: number;
  start: number; // sequence ms
  end: number;
  duration: number; // sequence ms
  overlapIn: number; // crossfade overlap with previous clip
}

export function fx(e: StoryEntry): Required<Effects> {
  const f = e.effects || {};
  return {
    speed: f.speed ?? 1,
    volume: f.volume ?? 1,
    muted: f.muted ?? false,
    brightness: f.brightness ?? 0,
    contrast: f.contrast ?? 1,
    saturation: f.saturation ?? 1,
    filter: f.filter ?? "none",
  };
}

export function bounds(e: StoryEntry): [number, number] {
  const s = e.trim_start_ms ?? e.start_ms ?? 0;
  const t = e.trim_end_ms ?? e.end_ms ?? s + 1000;
  return [s, t];
}

export function layout(entries: StoryEntry[]): { items: LaidOut[]; total: number } {
  const items: LaidOut[] = [];
  let total = 0;
  entries.forEach((entry, index) => {
    const [srcIn, srcOut] = bounds(entry);
    const speed = fx(entry).speed;
    const duration = Math.max(300, srcOut - srcIn) / speed;
    let overlap = 0;
    if (index > 0 && entry.transition_in && entry.transition_in !== "cut") {
      const prev = items[index - 1];
      overlap = Math.max(50, Math.min(TRANSITION_MS, prev.duration / 2, duration / 2, total / 2));
    }
    const start = total - overlap;
    items.push({ entry, index, srcIn, srcOut, speed, start, end: start + duration, duration, overlapIn: overlap });
    total = start + duration;
  });
  return { items, total };
}

export function itemAt(items: LaidOut[], t: number): LaidOut | null {
  for (let i = items.length - 1; i >= 0; i--) {
    if (t >= items[i].start + (i > 0 ? items[i].overlapIn / 2 : 0)) return items[i];
  }
  return items[0] || null;
}

/** Source-clip time (ms) under sequence time t for a laid-out item. */
export function sourceTimeAt(item: LaidOut, t: number): number {
  return Math.min(item.srcOut, Math.max(item.srcIn, item.srcIn + (t - item.start) * item.speed));
}

export function toRows(entries: StoryEntry[]): TimelineRow[] {
  return entries
    .filter((e) => e.segment_id)
    .map((e) => ({
      segment_id: e.segment_id!,
      narrative_role: e.narrative_role,
      transition_in: e.transition_in,
      trim_start_ms: e.trim_start_ms,
      trim_end_ms: e.trim_end_ms,
      effects: e.effects ?? null,
      reframe_params: e.reframe_params ?? null,
      edit_reasoning: e.edit_reasoning,
    }));
}

/** CSS approximation of the ffmpeg color effects, for live preview. */
export function cssFilter(e: Required<Effects>): string {
  const parts = [
    `brightness(${(1 + e.brightness * 1.6).toFixed(3)})`,
    `contrast(${e.contrast.toFixed(3)})`,
    `saturate(${e.saturation.toFixed(3)})`,
  ];
  const preset: Record<string, string> = {
    bw: "grayscale(1)",
    warm: "sepia(0.18) saturate(1.1) hue-rotate(-6deg)",
    cool: "hue-rotate(12deg) saturate(0.92)",
    vivid: "saturate(1.35) contrast(1.08)",
    vintage: "sepia(0.4) contrast(0.92) brightness(1.04)",
    cinematic: "contrast(1.12) saturate(0.9) hue-rotate(4deg)",
  };
  if (preset[e.filter]) parts.push(preset[e.filter]);
  return parts.join(" ");
}

/** m:ss, or broadcast timecode HH:MM:SS:FF (30 fps) when withFrames is set. */
export function fmtTime(ms: number, withFrames = false): string {
  const neg = ms < 0;
  ms = Math.max(0, Math.abs(ms));
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (!withFrames) return `${neg ? "-" : ""}${h ? `${h}:${pad(m)}` : m}:${pad(s)}`;
  const f = Math.floor(((ms % 1000) / 1000) * 30);
  return `${neg ? "-" : ""}${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`;
}

/** Subject x (0..1) at a source-clip time, from a tracked path (linear interpolation). */
export function subjectXAt(points: [number, number, number][] | undefined, srcMs: number): number {
  if (!points || points.length === 0) return 0.5;
  if (srcMs <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    if (srcMs <= points[i][0]) {
      const [t0, x0] = points[i - 1];
      const [t1, x1] = points[i];
      return x0 + ((x1 - x0) * (srcMs - t0)) / Math.max(1, t1 - t0);
    }
  }
  return points[points.length - 1][1];
}

/** Music gain at sequence time t (fades), mirroring the renderer. */
export function musicGain(
  t: number,
  start: number,
  length: number,
  volume: number,
  fadeIn: number,
  fadeOut: number
): number {
  const local = t - start;
  if (local < 0 || local > length) return 0;
  let g = volume;
  const fi = Math.min(fadeIn, length / 2);
  const fo = Math.min(fadeOut, length / 2);
  if (fi > 0 && local < fi) g *= local / fi;
  if (fo > 0 && local > length - fo) g *= Math.max(0, (length - local) / fo);
  return Math.max(0, Math.min(1, g));
}
