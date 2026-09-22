"use client";

import { useRef, useState } from "react";
import { AudioTrack, Clip, Segment } from "@/lib/api";
import { fmtTime } from "@/lib/timeline";

type Tab = "scenes" | "clips" | "music";

interface Props {
  segments: Segment[];
  clips: Clip[];
  audio: AudioTrack[];
  busy?: boolean;
  importing?: string | null;
  onAddSegment: (segmentId: string) => void;
  onAddClip: (clipId: string) => void;
  onImportVideos: (files: File[]) => void;
  onImportAudio: (file: File) => void;
  onPrepare: () => void;
  onPreview: (url: string, startMs: number) => void;
}

export default function MediaBin(p: Props) {
  const [tab, setTab] = useState<Tab>("scenes");
  const videoInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const unprepared = p.clips.filter((c) => !p.segments.some((s) => s.clip_id === c.id));
  const clipName = (id: string) => p.clips.find((c) => c.id === id)?.original_filename ?? "clip";

  return (
    <div className="glass flex h-full flex-col rounded-2xl">
      <div className="flex items-center gap-0.5 border-b border-border p-1.5">
        {(["scenes", "clips", "music"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`whitespace-nowrap rounded-lg px-2 py-1 text-[11px] font-medium capitalize transition ${
              tab === t ? "bg-accent/25 text-white" : "text-slate-400 hover:text-white"
            }`}
          >
            {t} <span className="text-slate-500">{t === "scenes" ? p.segments.length : t === "clips" ? p.clips.length : p.audio.length}</span>
          </button>
        ))}
        <div className="ml-auto flex shrink-0 gap-1">
          <input ref={videoInput} type="file" accept="video/*" multiple hidden
            onChange={(e) => { if (e.target.files?.length) p.onImportVideos(Array.from(e.target.files)); e.target.value = ""; }} />
          <input ref={audioInput} type="file" accept="audio/*,video/*" hidden
            onChange={(e) => { if (e.target.files?.[0]) p.onImportAudio(e.target.files[0]); e.target.value = ""; }} />
          <button className="btn-ghost whitespace-nowrap !px-1.5 !py-1 !text-[11px]" disabled={!!p.importing}
            onClick={() => videoInput.current?.click()} title="Import video clips">
            ＋🎬
          </button>
          <button className="btn-ghost whitespace-nowrap !px-1.5 !py-1 !text-[11px]" disabled={!!p.importing}
            onClick={() => audioInput.current?.click()} title="Add a song / voice-over">
            ＋🎵
          </button>
        </div>
      </div>

      {p.importing && (
        <div className="flex items-center gap-2 border-b border-border bg-accent/10 px-3 py-1.5 text-[11px] text-slate-300">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" /> {p.importing}
        </div>
      )}

      {unprepared.length > 0 && (
        <div className="flex items-center justify-between gap-2 border-b border-border bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-200">
          <span>{unprepared.length} uploaded clip(s) aren&apos;t ready for manual editing yet.</span>
          <button className="btn-ghost !px-2 !py-0.5 !text-[10px]" disabled={p.busy || !!p.importing} onClick={p.onPrepare}>
            Prepare (no AI)
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {tab === "scenes" && (
          p.segments.length === 0 ? (
            <Empty>No scenes yet — import a video, or run the AI pipeline.</Empty>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-2">
              {p.segments.map((s) => (
                <Card key={s.id} thumb={s.keyframe_url}
                  title={`${fmtTime(s.start_ms)}–${fmtTime(s.end_ms)}`}
                  sub={`${clipName(s.clip_id)} · q ${s.quality_score?.toFixed(2) ?? "—"}`}
                  badge={s.on_timeline ? "on timeline" : undefined}
                  onPreview={s.source_url ? () => p.onPreview(s.source_url!, s.start_ms) : undefined}
                  onAdd={() => p.onAddSegment(s.id)} busy={p.busy} />
              ))}
            </div>
          )
        )}
        {tab === "clips" && (
          p.clips.length === 0 ? (
            <Empty>No clips yet — use ＋🎬 to import video.</Empty>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-2">
              {p.clips.map((c) => (
                <Card key={c.id} thumb={c.thumbnail_url ?? null} title={c.original_filename}
                  sub={c.duration_ms ? `${(c.duration_ms / 1000).toFixed(1)}s · ${c.width}×${c.height}` : "not prepared"}
                  onPreview={c.source_url ? () => p.onPreview(c.source_url!, 0) : undefined}
                  onAdd={c.duration_ms ? () => p.onAddClip(c.id) : undefined} addLabel="+ Whole clip" busy={p.busy} />
              ))}
            </div>
          )
        )}
        {tab === "music" && (
          p.audio.length === 0 ? (
            <Empty>Add a song or voice-over with ＋🎵. It plays under your video, with fades and ducking.</Empty>
          ) : (
            <ul className="space-y-1.5">
              {p.audio.map((a) => (
                <li key={a.id} className="flex items-center justify-between rounded-lg border border-border bg-surface2 px-2.5 py-1.5 text-[11px]">
                  <span className="truncate text-slate-200">🎵 {a.original_filename}</span>
                  <span className="shrink-0 font-mono text-slate-500">{a.duration_ms ? fmtTime(a.duration_ms) : ""}</span>
                </li>
              ))}
              <li className="px-1 text-[10px] text-slate-500">Select the bar on the 🎵 Music lane to adjust it.</li>
            </ul>
          )
        )}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border border-dashed border-border py-8 text-center text-[11px] text-slate-500">{children}</div>;
}

function Card({ thumb, title, sub, badge, onAdd, onPreview, addLabel = "+ Add", busy }: {
  thumb: string | null; title: string; sub: string; badge?: string;
  onAdd?: () => void; onPreview?: () => void; addLabel?: string; busy?: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-surface2 transition hover:border-accent/50">
      <button className="relative block aspect-video w-full bg-black" onClick={onPreview} disabled={!onPreview} title="Preview source">
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumb} alt="" className="h-full w-full object-cover" />
        ) : (
          <span className="flex h-full items-center justify-center text-[9px] text-slate-600">no preview</span>
        )}
        {badge && <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[8px] text-emerald-300">{badge}</span>}
      </button>
      <div className="px-1.5 py-1">
        <p className="truncate text-[10px] font-medium text-slate-200">{title}</p>
        <p className="truncate text-[9px] text-slate-500">{sub}</p>
        {onAdd && (
          <button onClick={onAdd} disabled={busy}
            className="mt-1 w-full rounded border border-accent/40 bg-accent/10 py-0.5 text-[10px] font-medium text-accent hover:bg-accent/20 disabled:opacity-50">
            {addLabel}
          </button>
        )}
      </div>
    </div>
  );
}
