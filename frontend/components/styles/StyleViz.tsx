"use client";

/**
 * Small visuals for learned editor styles (dark UI).
 * Categorical slots = the validated reference palette, dark steps, checked
 * against the app surface #11131d (scripts/validate_palette.js: all pass,
 * worst adjacent CVD ΔE 8.4). Assigned in fixed order — never cycled; past
 * slot 7 everything folds into a neutral "Other".
 */
export const SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const OTHER = "#5f5e5a";
const KEEP = "#3987e5"; // diverging pole (blue)
const CUT = "#e66767"; // diverging pole (red)

export const STATUS_PILL: Record<string, string> = {
  draft: "bg-slate-500/15 text-slate-300",
  analyzing: "bg-amber-500/15 text-amber-300",
  training: "bg-amber-500/15 text-amber-300",
  ready: "bg-emerald-500/15 text-emerald-300",
  failed: "bg-red-500/15 text-red-300",
};

/** Mirrors backend/style/features.py FEATURE_LABELS. */
export const FEATURE_LABELS: Record<string, string> = {
  log_duration: "longer shots", rel_position: "later in the clip", sharpness: "sharp footage",
  brightness: "bright footage", contrast: "high contrast", saturation: "colorful footage",
  motion: "lots of motion", motion_var: "changing motion", audio_db: "loud audio",
  audio_peak_db: "audio peaks", audio_activity: "continuous sound / speech",
  faces: "people on screen", face_area: "close-ups of faces",
  sharpness_pct: "sharpest part of a clip", motion_pct: "most dynamic part of a clip",
  audio_pct: "loudest part of a clip", brightness_pct: "brightest part of a clip",
};

const TRANSITION_ORDER =["cut", "dissolve", "cross_fade", "fade_to_black", "wipe", "zoom_in", "zoom_out"];

export function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-extrabold text-white">{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}

/** Stacked proportion bar with a legend (identity never color-alone). */
export function TransitionMix({ mix }: { mix: Record<string, number> }) {
  const rows = TRANSITION_ORDER.map((k, i) => ({ k, v: mix[k] ?? 0, color: SLOTS[i] })).filter((r) => r.v > 0);
  if (!rows.length) return <p className="text-xs text-slate-500">No transitions measured.</p>;
  return (
    <div>
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded" role="img"
        aria-label={rows.map((r) => `${r.k.replace(/_/g, " ")} ${Math.round(r.v * 100)}%`).join(", ")}>
        {rows.map((r, i) => (
          <div key={r.k} title={`${r.k.replace(/_/g, " ")}: ${Math.round(r.v * 100)}%`}
            className={`h-full ${i === 0 ? "rounded-l" : ""} ${i === rows.length - 1 ? "rounded-r" : ""}`}
            style={{ width: `${r.v * 100}%`, background: r.color, minWidth: 3 }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-300">
        {rows.map((r) => (
          <li key={r.k} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: r.color }} />
            <span className="capitalize">{r.k.replace(/_/g, " ")}</span>
            <span className="text-slate-500">{Math.round(r.v * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Diverging bars around a neutral zero: blue = makes the editor keep a shot, red = makes them cut it. */
export function FeatureWeights({ weights, labels }: { weights: { feature: string; weight: number }[]; labels: Record<string, string> }) {
  const rows = weights.filter((w) => Math.abs(w.weight) >= 0.05).slice(0, 8);
  if (!rows.length) return <p className="text-xs text-slate-500">Not enough varied decisions yet to learn preferences.</p>;
  const max = Math.max(...rows.map((r) => Math.abs(r.weight)), 0.01);
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-[10px] text-slate-500">
        <span>← makes them cut</span>
        <span>makes them keep →</span>
      </div>
      <ul className="space-y-1.5">
        {rows.map((r) => {
          const pct = (Math.abs(r.weight) / max) * 50;
          const keep = r.weight > 0;
          return (
            <li key={r.feature} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] items-center gap-2 text-[11px]"
              title={`${labels[r.feature] || r.feature}: weight ${r.weight.toFixed(2)}`}>
              <span className="truncate text-right text-slate-300">{labels[r.feature] || r.feature}</span>
              <div className="relative h-2.5">
                <div className="absolute inset-y-0 left-1/2 w-px bg-[#383835]" />
                <div className="absolute inset-y-0" style={{
                  left: keep ? "50%" : `${50 - pct}%`, width: `${pct}%`, background: keep ? KEEP : CUT,
                  borderRadius: keep ? "0 4px 4px 0" : "4px 0 0 4px",
                }} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The finished edit as a strip of shots, each colored by the raw clip it came
 * from (its width = its duration). Unmatched shots are hatched neutral.
 */
export function EditMap({ shots, rawNames, durationMs }: {
  shots: { start_ms: number; end_ms: number; matched: boolean; clip: number; src_in: number; src_out: number; speed: number; transition_in: string }[];
  rawNames: string[];
  durationMs: number;
}) {
  const color = (clip: number) => (clip < 0 ? OTHER : clip < 7 ? SLOTS[clip] : OTHER);
  const used = Array.from(new Set(shots.filter((s) => s.matched).map((s) => s.clip))).sort((a, b) => a - b);
  return (
    <div>
      <div className="flex h-6 w-full gap-[2px]" role="img" aria-label={`${shots.length} shots`}>
        {shots.map((s, i) => (
          <div key={i}
            title={s.matched
              ? `${(s.start_ms / 1000).toFixed(1)}s–${(s.end_ms / 1000).toFixed(1)}s ← ${rawNames[s.clip] ?? `clip ${s.clip + 1}`} @ ${(s.src_in / 1000).toFixed(1)}s${s.speed !== 1 ? ` · ${s.speed}x` : ""}${s.transition_in !== "cut" ? ` · ${s.transition_in.replace(/_/g, " ")}` : ""}`
              : `${(s.start_ms / 1000).toFixed(1)}s–${(s.end_ms / 1000).toFixed(1)}s · not found in raw clips`}
            className="h-full rounded-[3px]"
            style={{
              width: `${((s.end_ms - s.start_ms) / Math.max(durationMs, 1)) * 100}%`, minWidth: 2,
              background: s.matched ? color(s.clip)
                : `repeating-linear-gradient(45deg, ${OTHER} 0 3px, transparent 3px 6px)`,
            }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400">
        {used.filter((c) => c < 7).map((c) => (
          <li key={c} className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: color(c) }} />
            <span className="max-w-[120px] truncate">{rawNames[c] ?? `clip ${c + 1}`}</span>
          </li>
        ))}
        {used.some((c) => c >= 7) && (
          <li className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: OTHER }} />Other clips</li>
        )}
        {shots.some((s) => !s.matched) && (
          <li className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: `repeating-linear-gradient(45deg, ${OTHER} 0 2px, transparent 2px 4px)` }} />
            not in raw footage
          </li>
        )}
      </ul>
    </div>
  );
}
