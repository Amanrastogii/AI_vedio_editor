"use client";

import { useEffect, useState } from "react";
import {
  CaptionCue, CaptionSettings, Job, TranscriptionStatus, downloadCaptions, editCaptionWords, getJob, startTranscribe,
} from "@/lib/api";
import { fmtTime } from "@/lib/timeline";

const PRESETS: { key: CaptionSettings["preset"]; label: string; hint: string }[] = [
  { key: "bold", label: "Bold", hint: "Big outlined text, spoken word highlighted" },
  { key: "karaoke", label: "Karaoke", hint: "Words fill with color as they're spoken" },
  { key: "classic", label: "Classic", hint: "Subtitles on a dark box" },
  { key: "minimal", label: "Minimal", hint: "Small text with a soft shadow" },
];

interface Props {
  projectId: string;
  cues: CaptionCue[];
  status: TranscriptionStatus | null;
  settings: CaptionSettings | null;
  t: number;
  onSettings: (patch: Partial<CaptionSettings>) => void;
  onReload: () => Promise<void>;
  onSeek: (t: number) => void;
}

export default function CaptionsPanel({ projectId, cues, status, settings, t, onSettings, onReload, onSeek }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const h = setInterval(async () => {
      try {
        const j = await getJob(projectId, job.id);
        setJob(j);
        if (j.status !== "running") {
          clearInterval(h);
          if (j.status === "failed") setError(j.error || "Transcription failed");
          await onReload();
        }
      } catch (e: any) {
        setError(e.message);
        clearInterval(h);
      }
    }, 1000);
    return () => clearInterval(h);
  }, [job, projectId, onReload]);

  async function generate(force = false) {
    setError("");
    try {
      setJob(await startTranscribe(projectId, force));
      if (!settings?.enabled) onSettings({ enabled: true });
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (!settings || !status) return <p className="text-[11px] text-slate-500">Loading captions…</p>;

  const running = job?.status === "running";
  const pending = status.clips - status.transcribed;

  return (
    <div className="space-y-3 text-[11px]">
      {!status.available ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200">
          Speech-to-text isn&apos;t installed on the server. Run <code className="font-mono">pip install faster-whisper</code>.
        </div>
      ) : (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-slate-300">
              {status.transcribed}/{status.clips} clips transcribed · {status.words} words
            </span>
            <button onClick={() => generate(false)} disabled={running || pending === 0}
              className="rounded-lg bg-gradient-to-r from-accent to-accent4 px-2.5 py-1 font-semibold text-white disabled:opacity-40">
              {running ? "Transcribing…" : pending ? "Generate captions" : "✓ Up to date"}
            </button>
          </div>
          {running && (
            <div className="mt-2">
              <div className="h-1.5 overflow-hidden rounded bg-white/10">
                <div className="h-full bg-gradient-to-r from-accent to-accent4 transition-all" style={{ width: `${Math.max(4, job!.progress * 100)}%` }} />
              </div>
              <p className="mt-1 text-slate-500">{job!.message}</p>
            </div>
          )}
          <p className="mt-1 text-[10px] text-slate-500">
            Runs locally with Whisper ({status.model}). Captions follow every trim, cut and speed change automatically.
            {status.transcribed > 0 && (
              <button className="ml-1 text-accent hover:underline" disabled={running} onClick={() => generate(true)}>re-transcribe</button>
            )}
          </p>
        </div>
      )}
      {error && <p className="text-red-300">{error}</p>}

      <label className="flex items-center justify-between rounded-lg border border-white/10 px-2 py-1.5 text-slate-200">
        <span className="font-semibold">Burn captions into the video</span>
        <input type="checkbox" checked={settings.enabled} onChange={(e) => onSettings({ enabled: e.target.checked })} />
      </label>

      <div className="grid grid-cols-2 gap-1.5">
        {PRESETS.map((p) => (
          <button key={p.key} onClick={() => onSettings({ preset: p.key })} title={p.hint}
            className={`rounded-lg border px-2 py-1.5 text-left ${settings.preset === p.key ? "border-accent bg-accent/20 text-white" : "border-white/10 text-slate-400 hover:border-accent/50"}`}>
            <span className="block font-semibold">{p.label}</span>
            <span className="block text-[9px] text-slate-500">{p.hint}</span>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-1">
        {(["top", "center", "bottom"] as const).map((pos) => (
          <button key={pos} onClick={() => onSettings({ position: pos })}
            className={`rounded border px-1 py-0.5 capitalize ${settings.position === pos ? "border-accent bg-accent/25 text-white" : "border-border text-slate-400"}`}>
            {pos}
          </button>
        ))}
      </div>

      <label className="block text-slate-400">
        <span className="flex justify-between"><span>Size</span><span className="font-mono text-slate-300">{settings.font_size}</span></span>
        <input type="range" min={28} max={120} step={2} defaultValue={settings.font_size} key={settings.font_size}
          onPointerUp={(e) => onSettings({ font_size: Number((e.target as HTMLInputElement).value) })}
          className="w-full accent-[#7c83f5]" />
      </label>

      <div className="flex flex-wrap items-center gap-3 text-slate-300">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={settings.uppercase} onChange={(e) => onSettings({ uppercase: e.target.checked })} /> ALL CAPS
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={settings.hide_fillers} onChange={(e) => onSettings({ hide_fillers: e.target.checked })} /> Hide um/uh
        </label>
        <label className="flex items-center gap-1.5">
          Highlight
          <input type="color" value={settings.highlight_color} onChange={(e) => onSettings({ highlight_color: e.target.value })}
            className="h-5 w-7 cursor-pointer rounded border border-border bg-transparent" />
        </label>
      </div>

      <div className="flex gap-1.5">
        <button className="btn-ghost !py-1 !text-[10px]" disabled={!cues.length} onClick={() => downloadCaptions(projectId, "srt").catch((e) => setError(e.message))}>⬇ SRT</button>
        <button className="btn-ghost !py-1 !text-[10px]" disabled={!cues.length} onClick={() => downloadCaptions(projectId, "vtt").catch((e) => setError(e.message))}>⬇ VTT</button>
      </div>

      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Captions ({cues.length}) — click text to fix a word
        </p>
        {cues.length === 0 ? (
          <p className="text-slate-500">{status.transcribed ? "No speech found in the clips on the timeline." : "Generate captions to see them here."}</p>
        ) : (
          <ul className="space-y-1">
            {cues.map((c, i) => (
              <CueRow key={`${c.start_ms}-${i}`} cue={c} active={t >= c.start_ms && t < c.end_ms} onSeek={onSeek}
                onSave={async (text) => {
                  setError("");
                  try {
                    await saveCue(projectId, c, text);
                    await onReload();
                  } catch (e: any) {
                    setError(e.message);
                  }
                }} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Same word count → fix words one by one (works across cuts); otherwise replace the cue's words in one go. */
async function saveCue(projectId: string, cue: CaptionCue, text: string) {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  const words = cue.words.filter((w) => w.id);
  if (!tokens.length || !words.length) return;
  if (tokens.length === words.length) {
    for (let i = 0; i < words.length; i++) {
      if (tokens[i] !== words[i].text) await editCaptionWords(projectId, [words[i].id!], tokens[i]);
    }
    return;
  }
  try {
    await editCaptionWords(projectId, words.map((w) => w.id!), tokens.join(" "));
  } catch (e: any) {
    throw new Error(/one clip/.test(e.message) ? "This caption spans a cut — keep the same number of words to fix it." : e.message);
  }
}

function CueRow({ cue, active, onSeek, onSave }: {
  cue: CaptionCue; active: boolean; onSeek: (t: number) => void; onSave: (text: string) => void;
}) {
  const plain = cue.words.map((w) => w.text).join(" ");
  const [text, setText] = useState(plain);
  useEffect(() => setText(plain), [plain]);
  return (
    <li className={`flex items-start gap-2 rounded-lg border px-2 py-1 ${active ? "border-accent/60 bg-accent/10" : "border-white/5"}`}>
      <button className="shrink-0 font-mono text-[10px] text-accent hover:underline" onClick={() => onSeek(cue.start_ms)}>
        {fmtTime(cue.start_ms)}
      </button>
      <input value={text} onChange={(e) => setText(e.target.value)}
        onBlur={() => text.trim() && text.trim() !== plain && onSave(text)}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        className="min-w-0 flex-1 bg-transparent text-slate-200 outline-none focus:text-white" />
    </li>
  );
}
