"use client";

import { Segment } from "@/lib/api";

interface Props {
  segments: Segment[];
  onAdd: (segmentId: string) => void;
  busy?: boolean;
}

export default function ClipBin({ segments, onAdd, busy }: Props) {
  const available = segments.filter((s) => !s.on_timeline);

  if (segments.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-surface/50 py-8 text-center text-xs text-slate-500">
        No segments yet — segments appear once Scene Detection has run.
      </div>
    );
  }

  if (available.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-surface/50 py-8 text-center text-xs text-slate-500">
        All detected segments are already on the timeline.
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {available.map((s) => (
        <div key={s.id} className="glass card-hover w-28 flex-shrink-0 rounded-xl p-1.5">
          <div className="relative aspect-video w-full overflow-hidden rounded bg-black">
            {s.keyframe_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={s.keyframe_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full items-center justify-center text-[9px] text-slate-600">no preview</div>
            )}
          </div>
          <p className="mt-1 text-[9px] text-slate-500">
            {((s.end_ms - s.start_ms) / 1000).toFixed(1)}s · q {s.quality_score?.toFixed(2) ?? "—"}
          </p>
          <button
            onClick={() => onAdd(s.id)}
            disabled={busy}
            className="mt-1 w-full rounded border border-accent/40 bg-accent/10 py-0.5 text-[10px] font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            + Add
          </button>
        </div>
      ))}
    </div>
  );
}
