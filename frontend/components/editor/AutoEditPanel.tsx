"use client";

import { useState } from "react";
import { AudioTrack, CleanupStats, EditorSettings, StoryEntry, runBeatSync, runCleanup } from "@/lib/api";

interface Props {
  projectId: string;
  audio: AudioTrack[];
  settings: EditorSettings | null;
  busy?: boolean;
  /** run an undoable timeline change; resolves to the new timeline (or undefined on failure) */
  onApply: (fn: () => Promise<StoryEntry[]>, notice: string) => Promise<void>;
  onFraming: (mode: EditorSettings["reframe"]["mode"]) => void;
}

export default function AutoEditPanel({ projectId, audio, settings, busy, onApply, onFraming }: Props) {
  const [minSilence, setMinSilence] = useState(0.7);
  const [padding, setPadding] = useState(0.15);
  const [silence, setSilence] = useState(true);
  const [fillers, setFillers] = useState(true);
  const [cleanPreview, setCleanPreview] = useState<CleanupStats | null>(null);
  const [trackId, setTrackId] = useState<string>(audio[0]?.id ?? "");
  const [every, setEvery] = useState<number | null>(null);
  const [beatPreview, setBeatPreview] = useState<string>("");
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState("");

  const cleanupOpts = (dry: boolean) => ({
    remove_silence: silence, min_silence_ms: Math.round(minSilence * 1000), padding_ms: Math.round(padding * 1000),
    remove_fillers: fillers, dry_run: dry,
  });

  async function previewCleanup() {
    setError("");
    setWorking("Analysing speech & silences…");
    try {
      setCleanPreview((await runCleanup(projectId, cleanupOpts(true))).stats);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWorking(null);
    }
  }

  async function applyCleanup() {
    setWorking("Cutting…");
    await onApply(async () => {
      const r = await runCleanup(projectId, cleanupOpts(false));
      if (!r.applied || !r.timeline) throw new Error("Nothing to remove.");
      return r.timeline;
    }, cleanPreview ? `Removed ${(cleanPreview.removed_ms / 1000).toFixed(1)}s — previous cut saved in Versions` : "Cleaned up");
    setCleanPreview(null);
    setWorking(null);
  }

  async function previewBeats() {
    if (!trackId) return;
    setError("");
    setWorking("Finding the beat…");
    try {
      const r = await runBeatSync(projectId, trackId, every, true);
      setBeatPreview(`${r.bpm.toFixed(0)} BPM · cut every ${r.stats.every} beat${r.stats.every > 1 ? "s" : ""} · ` +
        `${r.stats.synced} cut(s) will snap` + (r.stats.unsynced ? `, ${r.stats.unsynced} clip(s) too short` : "") +
        (r.stats.mean_error_ms != null ? ` · ±${r.stats.mean_error_ms.toFixed(0)}ms` : ""));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWorking(null);
    }
  }

  async function applyBeats() {
    setWorking("Syncing…");
    await onApply(async () => {
      const r = await runBeatSync(projectId, trackId, every, false);
      return r.timeline!;
    }, "Cuts synced to the beat — previous cut saved in Versions");
    setBeatPreview("");
    setWorking(null);
  }

  return (
    <div className="space-y-3">
      {working && (
        <div className="flex items-center gap-2 text-slate-300">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" /> {working}
        </div>
      )}
      {error && <p className="text-red-300">{error}</p>}

      <section>
        <p className="font-semibold text-slate-100">✂ Remove silences & filler words</p>
        <p className="mb-1.5 text-[10px] text-slate-500">Jump-cuts out pauses and “um/uh” in clips with speech. B-roll is never touched.</p>
        <div className="flex gap-3 text-slate-300">
          <label className="flex items-center gap-1"><input type="checkbox" checked={silence} onChange={(e) => { setSilence(e.target.checked); setCleanPreview(null); }} /> Silences</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={fillers} onChange={(e) => { setFillers(e.target.checked); setCleanPreview(null); }} /> Fillers</label>
        </div>
        {silence && (
          <div className="mt-1 grid grid-cols-2 gap-2 text-slate-400">
            <label>Pauses longer than <b className="text-slate-200">{minSilence.toFixed(1)}s</b>
              <input type="range" min={0.3} max={3} step={0.1} value={minSilence} className="w-full accent-[#7c83f5]"
                onChange={(e) => { setMinSilence(Number(e.target.value)); setCleanPreview(null); }} />
            </label>
            <label>Keep <b className="text-slate-200">{padding.toFixed(2)}s</b> breathing room
              <input type="range" min={0} max={0.5} step={0.05} value={padding} className="w-full accent-[#7c83f5]"
                onChange={(e) => { setPadding(Number(e.target.value)); setCleanPreview(null); }} />
            </label>
          </div>
        )}
        {cleanPreview && (
          <p className="mt-1.5 rounded bg-accent/10 px-2 py-1 text-slate-200">
            Removes <b>{(cleanPreview.removed_ms / 1000).toFixed(1)}s</b>: {cleanPreview.silences_removed} pause(s),{" "}
            {cleanPreview.fillers_removed} filler word(s){cleanPreview.removed_words.length ? ` (${cleanPreview.removed_words.slice(0, 6).join(", ")})` : ""}
            {" "}→ {cleanPreview.cuts_added} new cut(s).
            {cleanPreview.clips_skipped_no_speech > 0 && <span className="text-slate-400"> {cleanPreview.clips_skipped_no_speech} clip(s) without speech left alone.</span>}
          </p>
        )}
        <div className="mt-1.5 flex gap-1.5">
          <button className="btn-ghost !py-1 !text-[11px]" disabled={!!working || busy || (!silence && !fillers)} onClick={previewCleanup}>Preview</button>
          <button className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-40"
            disabled={!!working || busy || !cleanPreview || cleanPreview.removed_ms <= 0} onClick={applyCleanup}>Apply</button>
        </div>
      </section>

      <section className="border-t border-white/10 pt-3">
        <p className="font-semibold text-slate-100">🥁 Sync cuts to the beat</p>
        {audio.length === 0 ? (
          <p className="text-[10px] text-slate-500">Add a song first (＋🎵 in the media bin).</p>
        ) : (
          <>
            <div className="mt-1 flex items-center gap-1.5">
              <select value={trackId} onChange={(e) => { setTrackId(e.target.value); setBeatPreview(""); }}
                className="min-w-0 flex-1 rounded border border-border bg-surface2 px-1.5 py-1 text-slate-200">
                {audio.map((a) => <option key={a.id} value={a.id}>{a.original_filename}</option>)}
              </select>
              <select value={every ?? "auto"} onChange={(e) => { setEvery(e.target.value === "auto" ? null : Number(e.target.value)); setBeatPreview(""); }}
                className="rounded border border-border bg-surface2 px-1.5 py-1 text-slate-200" aria-label="Cut every">
                <option value="auto">Auto</option>
                <option value="1">Every beat</option>
                <option value="2">Every 2 beats</option>
                <option value="4">Every bar</option>
              </select>
            </div>
            {beatPreview && <p className="mt-1.5 rounded bg-accent/10 px-2 py-1 text-slate-200">{beatPreview}</p>}
            <div className="mt-1.5 flex gap-1.5">
              <button className="btn-ghost !py-1 !text-[11px]" disabled={!!working || busy || !trackId} onClick={previewBeats}>Preview</button>
              <button className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-40"
                disabled={!!working || busy || !beatPreview} onClick={applyBeats}>Apply</button>
            </div>
          </>
        )}
      </section>

      <section className="border-t border-white/10 pt-3">
        <p className="font-semibold text-slate-100">📐 Framing for vertical / square outputs</p>
        <p className="mb-1.5 text-[10px] text-slate-500">When a 16:9 clip goes into a 9:16 reel.</p>
        <div className="grid grid-cols-3 gap-1">
          {([["smart", "Follow subject"], ["center", "Center crop"], ["fit", "Fit (bars)"]] as const).map(([m, label]) => (
            <button key={m} onClick={() => onFraming(m)}
              className={`rounded border px-1 py-1 ${settings?.reframe.mode === m ? "border-accent bg-accent/25 text-white" : "border-border text-slate-400 hover:border-accent/50"}`}>
              {label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
