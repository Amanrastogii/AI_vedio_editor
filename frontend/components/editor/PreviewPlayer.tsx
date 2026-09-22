"use client";

import { useEffect, useRef } from "react";
import { AudioTrack, CaptionCue, CaptionSettings, SubjectTrack, TextOverlay } from "@/lib/api";
import { LaidOut, cssFilter, fmtTime, fx, itemAt, musicGain, sourceTimeAt, subjectXAt } from "@/lib/timeline";

interface Props {
  items: LaidOut[];
  total: number;
  t: number;
  playing: boolean;
  onTime: (t: number) => void;
  onPlayingChange: (p: boolean) => void;
  audio: AudioTrack[];
  texts: TextOverlay[];
  aspect: "16:9" | "9:16";
  cues?: CaptionCue[];
  captions?: CaptionSettings | null;
  tracks?: Record<string, SubjectTrack>;
  reframeMode?: "smart" | "center" | "fit";
}

/** Live captions in the chosen preset — sized in container units so they scale with the frame like the render. */
function CaptionOverlay({ cues, cfg, t }: { cues: CaptionCue[]; cfg: CaptionSettings; t: number }) {
  const cue = cues.find((c) => t >= c.start_ms && t < c.end_ms);
  if (!cue || !cfg.enabled) return null;
  const size = `${((cfg.font_size / 1080) * 100 * (cfg.preset === "minimal" ? 0.8 : 1)).toFixed(2)}cqmin`;
  const pos = cfg.position === "top" ? { top: "7%" } : cfg.position === "center" ? { top: "50%", transform: "translateY(-50%)" } : { bottom: "12%" };
  const activeIdx = cue.words.findIndex((w) => t >= w.start_ms && t < w.end_ms);
  const outline = cfg.preset === "minimal" ? "0 1px 3px rgba(0,0,0,.8)"
    : "-2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 2px 4px rgba(0,0,0,.6)";
  return (
    <div className="pointer-events-none absolute inset-x-[6%] z-10 text-center leading-tight" style={pos}>
      <span
        className={cfg.preset === "classic" ? "rounded bg-black/70 px-2 py-0.5" : ""}
        style={{
          fontSize: size, fontWeight: cfg.preset === "classic" || cfg.preset === "minimal" ? 500 : 800,
          color: cfg.text_color, textShadow: cfg.preset === "classic" ? undefined : outline,
          textTransform: cfg.uppercase ? "uppercase" : undefined, boxDecorationBreak: "clone",
        }}
      >
        {cue.words.map((w, i) => {
          const on = cfg.preset === "bold" ? i === activeIdx : cfg.preset === "karaoke" ? t >= w.start_ms : false;
          return (
            <span key={i} style={on ? { color: cfg.highlight_color } : undefined}>
              {i ? " " : ""}
              {w.text}
            </span>
          );
        })}
      </span>
    </div>
  );
}

/**
 * Live sequence preview straight from the source clips (no render needed).
 * Two <video> elements are double-buffered so the next clip is already
 * loaded and seeked when a cut happens. Crossfades preview as cuts; the
 * render has the real transitions.
 */
export default function PreviewPlayer({
  items, total, t, playing, onTime, onPlayingChange, audio, texts, aspect, cues = [], captions, tracks, reframeMode = "smart",
}: Props) {
  const frameRef = useRef({ aspect, tracks, reframeMode });
  frameRef.current = { aspect, tracks, reframeMode };
  const vids = [useRef<HTMLVideoElement>(null), useRef<HTMLVideoElement>(null)];
  const active = useRef(0);
  const curIdx = useRef(-1);
  const raf = useRef<number>(0);
  const audioEls = useRef<Record<string, HTMLAudioElement | null>>({});
  const tRef = useRef(t);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const audioRef = useRef(audio);
  audioRef.current = audio;
  const totalRef = useRef(total);
  totalRef.current = total;

  function setSrc(v: HTMLVideoElement, url: string, atSec: number) {
    if (v.dataset.src !== url) {
      v.dataset.src = url;
      v.src = url;
      v.preload = "auto";
      const seek = () => {
        v.currentTime = atSec;
        v.removeEventListener("loadedmetadata", seek);
      };
      v.addEventListener("loadedmetadata", seek);
    } else if (Math.abs(v.currentTime - atSec) > 0.04) {
      v.currentTime = atSec;
    }
  }

  function musicActiveAt(time: number) {
    return audioRef.current.some((a) => {
      const len = a.length_ms ?? totalRef.current - a.start_ms;
      return time >= a.start_ms && time <= a.start_ms + len && a.duck_original < 0.999;
    })
      ? Math.min(...audioRef.current.map((a) => a.duck_original))
      : 1;
  }

  function applyLook(v: HTMLVideoElement, item: LaidOut, time: number) {
    const e = fx(item.entry);
    v.style.filter = cssFilter(e);
    v.playbackRate = Math.min(4, Math.max(0.25, item.speed));
    v.muted = e.muted;
    v.volume = Math.max(0, Math.min(1, e.volume * musicActiveAt(time)));
    applyFraming(v, item, time);
  }

  /** Mirror the render's reframing: follow the tracked subject / centre / fit / manual x. */
  function applyFraming(v: HTMLVideoElement, item: LaidOut, time: number) {
    const { aspect: a, tracks: tr, reframeMode: mode } = frameRef.current;
    const frameA = a === "9:16" ? 9 / 16 : 16 / 9;
    const srcA = v.videoWidth && v.videoHeight ? v.videoWidth / v.videoHeight : frameA;
    const rp = item.entry.reframe_params;
    const m = rp?.mode || mode;
    if (Math.abs(srcA - frameA) / frameA < 0.03 || m === "fit") {
      v.style.objectFit = "contain";
      v.style.objectPosition = "50% 50%";
      return;
    }
    v.style.objectFit = "cover";
    let x = 0.5;
    if (m === "manual") x = rp?.x ?? 0.5;
    else if (m === "smart" && item.entry.clip_id) x = subjectXAt(tr?.[item.entry.clip_id]?.points, sourceTimeAt(item, time));
    if (frameA < srcA) {
      const f = frameA / srcA; // visible fraction of the source width
      const left = Math.min(1 - f, Math.max(0, x - f / 2));
      v.style.objectPosition = `${((left / (1 - f)) * 100).toFixed(1)}% 50%`;
    } else {
      v.style.objectPosition = "50% 50%";
    }
  }

  /** Show `item` at sequence time `time` on the active buffer; preload the next clip on the other. */
  function show(item: LaidOut, time: number) {
    const cur = vids[active.current].current;
    const other = vids[1 - active.current].current;
    if (!cur || !other || !item.entry.source_url) return;
    const at = sourceTimeAt(item, time) / 1000;
    if (curIdx.current !== item.index) {
      // Swap to the buffer that already holds this clip, if it does.
      if (other.dataset.src === item.entry.source_url && other.dataset.idx === String(item.index)) {
        active.current = 1 - active.current;
      }
      curIdx.current = item.index;
    }
    const v = vids[active.current].current!;
    const hidden = vids[1 - active.current].current!;
    setSrc(v, item.entry.source_url, at);
    v.dataset.idx = String(item.index);
    applyLook(v, item, time);
    v.style.opacity = "1";
    hidden.style.opacity = "0";
    hidden.pause();
    const next = itemsRef.current[item.index + 1];
    if (next?.entry.source_url) {
      setSrc(hidden, next.entry.source_url, next.srcIn / 1000 + (next.overlapIn / 2 / 1000) * next.speed);
      hidden.dataset.idx = String(next.index);
    }
  }

  function syncAudio(time: number, isPlaying: boolean) {
    for (const a of audioRef.current) {
      const el = audioEls.current[a.id];
      if (!el) continue;
      const len = a.length_ms ?? totalRef.current - a.start_ms;
      const inside = time >= a.start_ms && time <= a.start_ms + len;
      if (!inside || !isPlaying) {
        if (!el.paused) el.pause();
        continue;
      }
      let pos = a.source_offset_ms + (time - a.start_ms);
      const dur = (a.duration_ms ?? 0) || el.duration * 1000;
      if (a.loop && dur > 0) pos = pos % dur;
      if (Math.abs(el.currentTime * 1000 - pos) > 300) el.currentTime = pos / 1000;
      el.volume = musicGain(time, a.start_ms, len, Math.min(1, a.volume), a.fade_in_ms, a.fade_out_ms);
      if (el.paused) el.play().catch(() => {});
    }
  }

  // Scrubbing / paused seeks (and re-frame when the aspect / framing / tracks change)
  useEffect(() => {
    tRef.current = t;
    if (playing) return;
    const item = itemAt(items, t);
    if (item) show(item, t);
    syncAudio(t, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, items, playing, aspect, tracks, reframeMode]);

  // a freshly loaded source only knows its dimensions after metadata → frame it then
  useEffect(() => {
    const handlers = vids.map((r) => {
      const v = r.current;
      if (!v) return () => {};
      const h = () => {
        const item = itemsRef.current[curIdx.current];
        if (item && v.dataset.idx === String(item.index)) applyFraming(v, item, tRef.current);
      };
      v.addEventListener("loadedmetadata", h);
      return () => v.removeEventListener("loadedmetadata", h);
    });
    return () => handlers.forEach((off) => off());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Playback loop
  useEffect(() => {
    if (!playing) {
      vids.forEach((r) => r.current?.pause());
      syncAudio(tRef.current, false);
      return;
    }
    if (!itemsRef.current.length) {
      onPlayingChange(false);
      return;
    }
    let startT = tRef.current >= totalRef.current - 50 ? 0 : tRef.current;
    const first = itemAt(itemsRef.current, startT)!;
    show(first, startT);
    vids[active.current].current?.play().catch(() => {});

    const tick = () => {
      const list = itemsRef.current;
      const item = list[curIdx.current];
      const v = vids[active.current].current;
      if (!item || !v) return;
      let seqT = item.start + (v.currentTime * 1000 - item.srcIn) / item.speed;
      seqT = Math.max(item.start, seqT);
      const next = list[item.index + 1];
      const switchAt = next ? next.start + next.overlapIn / 2 : item.end;
      if (seqT >= switchAt - 15 || v.ended) {
        if (!next) {
          onTime(totalRef.current);
          onPlayingChange(false);
          return;
        }
        show(next, switchAt);
        vids[active.current].current?.play().catch(() => {});
        seqT = switchAt;
      }
      tRef.current = seqT;
      onTime(seqT);
      applyLook(vids[active.current].current!, list[curIdx.current], seqT);
      syncAudio(seqT, true);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  const visibleTexts = texts.filter((x) => t >= x.start_ms && t < x.end_ms);
  const vertical = aspect === "9:16";

  return (
    <div className="flex h-full flex-col">
      <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-xl bg-black">
        <div
          className="relative overflow-hidden bg-black"
          style={{
            ...(vertical ? { height: "100%", aspectRatio: "9 / 16" } : { width: "100%", aspectRatio: "16 / 9", maxHeight: "100%" }),
            containerType: "size",
          }}
        >
          {vids.map((r, i) => (
            <video
              key={i}
              ref={r}
              playsInline
              className="absolute inset-0 h-full w-full object-contain transition-opacity duration-75"
              style={{ opacity: i === 0 ? 1 : 0 }}
            />
          ))}
          {visibleTexts.map((x) => (
            <div
              key={x.id}
              className="pointer-events-none absolute inset-x-0 flex justify-center px-2"
              style={{
                top: x.position === "top" ? "8%" : x.position === "center" ? "50%" : undefined,
                bottom: x.position === "bottom" ? "14%" : undefined,
                transform: x.position === "center" ? "translateY(-50%)" : undefined,
              }}
            >
              <span
                className="rounded px-2 py-0.5 text-center font-bold leading-tight"
                style={{
                  color: x.color,
                  fontSize: `clamp(9px, ${(x.font_size / 1080) * (vertical ? 56 : 32)}vh, 64px)`,
                  background: x.box ? "rgba(0,0,0,0.45)" : undefined,
                  textShadow: "0 0 3px rgba(0,0,0,0.8)",
                }}
              >
                {x.text}
              </span>
            </div>
          ))}
          {captions && <CaptionOverlay cues={cues} cfg={captions} t={t} />}
          {items.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-center text-xs text-slate-500">
              <span>Timeline is empty</span>
              <span>Add clips from the media bin to start editing.</span>
            </div>
          )}
        </div>
        {audio.map((a) => (
          <audio key={a.id} ref={(el) => { audioEls.current[a.id] = el; }} src={a.url || undefined} preload="auto" />
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-400">
        <button
          onClick={() => onPlayingChange(!playing)}
          disabled={!items.length}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-r from-accent to-accent4 text-sm text-white disabled:opacity-40"
          title="Play / pause (Space)"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <button className="btn-ghost !px-2 !py-1" onClick={() => onTime(0)} title="Go to start (Home)" aria-label="Go to start">
          ⏮
        </button>
        <button className="btn-ghost !px-2 !py-1" onClick={() => onTime(Math.max(0, t - 1000 / 30))} title="Back one frame (←)" aria-label="Back one frame">
          ◂
        </button>
        <button className="btn-ghost !px-2 !py-1" onClick={() => onTime(Math.min(total, t + 1000 / 30))} title="Forward one frame (→)" aria-label="Forward one frame">
          ▸
        </button>
        <span className="font-mono text-slate-200">{fmtTime(t, true)}</span>
        <span className="font-mono text-slate-500">/ {fmtTime(total, true)}</span>
      </div>
    </div>
  );
}
