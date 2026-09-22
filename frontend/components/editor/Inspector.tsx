"use client";

import { useEffect, useState } from "react";
import { AudioTrack, Effects, ReframeParams, StoryEntry, TextOverlay } from "@/lib/api";
import { FILTERS, LaidOut, ROLES, TRANSITIONS, fmtTime, fx } from "@/lib/timeline";

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

interface ClipProps {
  item: LaidOut;
  playheadSrcMs: number | null; // source time under the playhead if it's inside this clip
  busy?: boolean;
  onEffects: (patch: Effects, commit: boolean) => void;
  onPatch: (patch: Partial<Pick<StoryEntry, "transition_in" | "narrative_role" | "trim_start_ms" | "trim_end_ms">>) => void;
  onSplit: () => void;
  onDuplicate: () => void;
  onJoin: () => void;
  onDelete: () => void;
  projectFraming: string;
  onReframe: (rp: ReframeParams | null) => void;
}

export function ClipInspector({
  item, playheadSrcMs, busy, onEffects, onPatch, onSplit, onDuplicate, onJoin, onDelete, projectFraming, onReframe,
}: ClipProps) {
  const e = item.entry;
  const f = fx(e);
  const segStart = e.start_ms ?? 0;
  const segEnd = e.end_ms ?? item.srcOut;

  return (
    <div className="space-y-3 text-[11px]">
      <Header title={`Clip ${item.index + 1}`} sub={`${fmtTime(item.start)} → ${fmtTime(item.end)} · ${(item.duration / 1000).toFixed(2)}s`} />

      <div className="grid grid-cols-2 gap-2">
        <Button onClick={onSplit} disabled={busy || playheadSrcMs == null} title="Cut this clip at the playhead (S)">✂ Split</Button>
        <Button onClick={onDuplicate} disabled={busy} title="Ctrl+D">⧉ Duplicate</Button>
        <Button onClick={onJoin} disabled={busy} title="Stitch with the next clip (same source, contiguous)">🔗 Join next</Button>
        <Button onClick={onDelete} disabled={busy} danger title="Delete">🗑 Delete</Button>
      </div>

      <Section title="Trim (source time)">
        <div className="grid grid-cols-2 gap-2">
          <NumField label="In (s)" value={(item.srcIn - segStart) / 1000} min={0} max={(item.srcOut - segStart) / 1000 - 0.2}
            onCommit={(v) => onPatch({ trim_start_ms: Math.round(segStart + v * 1000) })} />
          <NumField label="Out (s)" value={(item.srcOut - segStart) / 1000} min={(item.srcIn - segStart) / 1000 + 0.2} max={(segEnd - segStart) / 1000}
            onCommit={(v) => onPatch({ trim_end_ms: Math.round(segStart + v * 1000) })} />
        </div>
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          <Button disabled={busy || playheadSrcMs == null} onClick={() => onPatch({ trim_start_ms: Math.round(playheadSrcMs!) })} title="I">⇤ In at playhead</Button>
          <Button disabled={busy || playheadSrcMs == null} onClick={() => onPatch({ trim_end_ms: Math.round(playheadSrcMs!) })} title="O">Out at playhead ⇥</Button>
        </div>
      </Section>

      <Section title="Speed & audio">
        <div className="flex flex-wrap gap-1">
          {SPEEDS.map((s) => (
            <button key={s} disabled={busy} onClick={() => onEffects({ speed: s }, true)}
              className={`rounded border px-1.5 py-0.5 ${f.speed === s ? "border-accent bg-accent/25 text-white" : "border-border text-slate-400 hover:border-accent/60"}`}>
              {s}x
            </button>
          ))}
        </div>
        <Slider label="Volume" value={f.volume} min={0} max={2} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`}
          onChange={(v, c) => onEffects({ volume: v }, c)} />
        <label className="mt-1 flex items-center gap-2 text-slate-300">
          <input type="checkbox" checked={f.muted} onChange={(ev) => onEffects({ muted: ev.target.checked }, true)} /> Mute clip audio
        </label>
      </Section>

      <Section title="Color" action={
        <button className="text-accent hover:underline" onClick={() => onEffects({ brightness: 0, contrast: 1, saturation: 1, filter: "none" }, true)}>reset</button>
      }>
        <Slider label="Brightness" value={f.brightness} min={-0.5} max={0.5} step={0.01} fmt={(v) => `${v > 0 ? "+" : ""}${Math.round(v * 100)}`}
          onChange={(v, c) => onEffects({ brightness: v }, c)} />
        <Slider label="Contrast" value={f.contrast} min={0.5} max={2} step={0.01} fmt={(v) => `${Math.round(v * 100)}%`}
          onChange={(v, c) => onEffects({ contrast: v }, c)} />
        <Slider label="Saturation" value={f.saturation} min={0} max={3} step={0.01} fmt={(v) => `${Math.round(v * 100)}%`}
          onChange={(v, c) => onEffects({ saturation: v }, c)} />
        <div className="mt-1.5 flex flex-wrap gap-1">
          {FILTERS.map((x) => (
            <button key={x} onClick={() => onEffects({ filter: x }, true)} disabled={busy}
              className={`rounded border px-1.5 py-0.5 capitalize ${f.filter === x ? "border-accent bg-accent/25 text-white" : "border-border text-slate-400 hover:border-accent/60"}`}>
              {x}
            </button>
          ))}
        </div>
      </Section>

      <Section title="Framing (vertical / square outputs)">
        <div className="grid grid-cols-5 gap-1">
          {([["", "Auto"], ["smart", "Follow"], ["center", "Center"], ["fit", "Fit"], ["manual", "Manual"]] as const).map(([m, label]) => {
            const cur = e.reframe_params?.mode || "";
            return (
              <button key={m} disabled={busy} title={m === "" ? `Project default (${projectFraming})` : undefined}
                onClick={() => onReframe(m === "" ? null : m === "manual" ? { mode: "manual", x: e.reframe_params?.x ?? 0.5 } : { mode: m })}
                className={`rounded border px-1 py-0.5 ${cur === m ? "border-accent bg-accent/25 text-white" : "border-border text-slate-400 hover:border-accent/60"}`}>
                {label}
              </button>
            );
          })}
        </div>
        {e.reframe_params?.mode === "manual" && (
          <Slider label="Horizontal position" value={e.reframe_params.x ?? 0.5} min={0} max={1} step={0.01}
            fmt={(v) => (v < 0.4 ? "left" : v > 0.6 ? "right" : "center") + ` ${Math.round(v * 100)}%`}
            onChange={(v, c) => c && onReframe({ mode: "manual", x: v })} />
        )}
      </Section>

      <Section title="Transition & story">
        <Select label="Transition in" value={e.transition_in} options={TRANSITIONS} disabled={busy || item.index === 0}
          onChange={(v) => onPatch({ transition_in: v })} />
        <Select label="Story role" value={e.narrative_role} options={ROLES} disabled={busy}
          onChange={(v) => onPatch({ narrative_role: v })} />
      </Section>

      {e.edit_reasoning && (
        <Section title="Why the AI chose this">
          <p className="leading-relaxed text-slate-400">{e.edit_reasoning}</p>
        </Section>
      )}
    </div>
  );
}

export function AudioInspector({ track, total, onPatch, onDelete, onDetectBeats, detecting }: {
  track: AudioTrack; total: number; onPatch: (p: Partial<AudioTrack>) => void; onDelete: () => void;
  onDetectBeats: () => void; detecting?: boolean;
}) {
  return (
    <div className="space-y-3 text-[11px]">
      <Header title="🎵 Music" sub={track.original_filename} />
      <div className="flex items-center justify-between rounded-lg border border-white/10 px-2 py-1.5">
        <span className="text-slate-300">
          {track.analysis?.bpm ? <>🥁 <b>{Math.round(track.analysis.bpm)} BPM</b> · {track.analysis.beats_ms.length} beats</> : "Tempo not analysed"}
        </span>
        <button className="btn-ghost !px-2 !py-0.5 !text-[10px]" disabled={detecting} onClick={onDetectBeats}>
          {detecting ? "Listening…" : track.analysis?.bpm ? "Show beats" : "Detect beats"}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <NumField label="Starts at (s)" value={track.start_ms / 1000} min={0} max={total / 1000} onCommit={(v) => onPatch({ start_ms: Math.round(v * 1000) })} />
        <NumField label="Skip into song (s)" value={track.source_offset_ms / 1000} min={0} max={(track.duration_ms ?? 600000) / 1000}
          onCommit={(v) => onPatch({ source_offset_ms: Math.round(v * 1000) })} />
        <NumField label="Length (s, 0 = to end)" value={(track.length_ms ?? 0) / 1000} min={0} max={3600}
          onCommit={(v) => onPatch({ length_ms: v > 0 ? Math.round(v * 1000) : null })} />
        <label className="flex items-end gap-2 pb-1 text-slate-300">
          <input type="checkbox" checked={track.loop} onChange={(e) => onPatch({ loop: e.target.checked })} /> Loop
        </label>
      </div>
      <Slider label="Music volume" value={track.volume} min={0} max={2} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`}
        onChange={(v, c) => c && onPatch({ volume: v })} />
      <Slider label="Clip audio under music (ducking)" value={track.duck_original} min={0} max={1} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`}
        onChange={(v, c) => c && onPatch({ duck_original: v })} />
      <div className="grid grid-cols-2 gap-2">
        <NumField label="Fade in (s)" value={track.fade_in_ms / 1000} min={0} max={20} onCommit={(v) => onPatch({ fade_in_ms: Math.round(v * 1000) })} />
        <NumField label="Fade out (s)" value={track.fade_out_ms / 1000} min={0} max={20} onCommit={(v) => onPatch({ fade_out_ms: Math.round(v * 1000) })} />
      </div>
      <Button danger onClick={onDelete}>🗑 Remove music</Button>
    </div>
  );
}

export function TextInspector({ overlay, onPatch, onDelete }: {
  overlay: TextOverlay; onPatch: (p: Partial<TextOverlay>) => void; onDelete: () => void;
}) {
  const [text, setText] = useState(overlay.text);
  useEffect(() => setText(overlay.text), [overlay.id, overlay.text]);
  return (
    <div className="space-y-3 text-[11px]">
      <Header title="🔤 Text" sub={`${fmtTime(overlay.start_ms)} → ${fmtTime(overlay.end_ms)}`} />
      <label className="block text-slate-400">
        Text
        <textarea value={text} rows={2} maxLength={300} onChange={(e) => setText(e.target.value)}
          onBlur={() => text.trim() && text !== overlay.text && onPatch({ text: text.trim() })}
          className="mt-0.5 w-full rounded border border-border bg-surface2 px-2 py-1 text-slate-100 outline-none focus:border-accent" />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <NumField label="Start (s)" value={overlay.start_ms / 1000} min={0} max={overlay.end_ms / 1000 - 0.1} onCommit={(v) => onPatch({ start_ms: Math.round(v * 1000) })} />
        <NumField label="End (s)" value={overlay.end_ms / 1000} min={overlay.start_ms / 1000 + 0.1} max={3600} onCommit={(v) => onPatch({ end_ms: Math.round(v * 1000) })} />
      </div>
      <Select label="Position" value={overlay.position} options={["top", "center", "bottom"]} onChange={(v) => onPatch({ position: v as TextOverlay["position"] })} />
      <Slider label="Size" value={overlay.font_size} min={12} max={200} step={2} fmt={(v) => `${v}px`} onChange={(v, c) => c && onPatch({ font_size: v })} />
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-slate-300">
          Color
          <input type="color" value={overlay.color.startsWith("#") ? overlay.color : "#ffffff"}
            onChange={(e) => onPatch({ color: e.target.value })} className="h-6 w-8 cursor-pointer rounded border border-border bg-transparent" />
        </label>
        <label className="flex items-center gap-2 text-slate-300">
          <input type="checkbox" checked={overlay.box} onChange={(e) => onPatch({ box: e.target.checked })} /> Background box
        </label>
      </div>
      <Button danger onClick={onDelete}>🗑 Remove text</Button>
    </div>
  );
}

// ── small controls ────────────────────────────────────────────────────────────

function Header({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="border-b border-border pb-2">
      <p className="text-sm font-bold text-white">{title}</p>
      {sub && <p className="truncate text-[10px] text-slate-500">{sub}</p>}
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        <span>{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function Button({ children, onClick, disabled, danger, title }: {
  children: React.ReactNode; onClick: () => void; disabled?: boolean; danger?: boolean; title?: string;
}) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className={`w-full rounded-lg border px-2 py-1.5 font-medium transition disabled:opacity-40 ${
        danger ? "border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/20"
          : "border-white/10 bg-white/5 text-slate-200 hover:border-accent/60 hover:bg-accent/10"}`}>
      {children}
    </button>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }: {
  label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string;
  onChange: (v: number, commit: boolean) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <label className="mt-1.5 block text-slate-400">
      <span className="flex justify-between"><span>{label}</span><span className="font-mono text-slate-300">{fmt(v)}</span></span>
      <input type="range" min={min} max={max} step={step} value={v} className="mt-0.5 w-full accent-[#7c83f5]"
        onChange={(e) => { const n = Number(e.target.value); setV(n); onChange(n, false); }}
        onPointerUp={() => onChange(v, true)}
        onKeyUp={() => onChange(v, true)} />
    </label>
  );
}

function NumField({ label, value, min, max, onCommit }: {
  label: string; value: number; min: number; max: number; onCommit: (v: number) => void;
}) {
  const [s, setS] = useState(value.toFixed(2));
  useEffect(() => setS(value.toFixed(2)), [value]);
  function commit() {
    const n = Number(s);
    if (!Number.isFinite(n)) return setS(value.toFixed(2));
    const c = Math.max(min, Math.min(max, n));
    if (Math.abs(c - value) > 0.005) onCommit(c);
    else setS(value.toFixed(2));
  }
  return (
    <label className="block text-slate-400">
      {label}
      <input value={s} inputMode="decimal" onChange={(e) => setS(e.target.value)} onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        className="mt-0.5 w-full rounded border border-border bg-surface2 px-2 py-1 font-mono text-slate-100 outline-none focus:border-accent" />
    </label>
  );
}

function Select({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void; disabled?: boolean;
}) {
  return (
    <label className="mt-1 block text-slate-400">
      {label}
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded border border-border bg-surface2 px-1.5 py-1 capitalize text-slate-200 disabled:opacity-50">
        {options.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
      </select>
    </label>
  );
}
