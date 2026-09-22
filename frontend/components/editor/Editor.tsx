"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AudioTrack, CaptionCue, CaptionSettings, Clip, EditorSettings, Effects, Output, Segment, StoryEntry, StyleProfile,
  SubjectTrack, TextOverlay, TimelineRow, TimelineVersion, TranscriptionStatus,
  addClipRange, addStoryEntry, addText, analyzeBeats, applyStyle, deleteAudio, deleteStoryEntry, deleteText,
  deleteVersion, duplicateEntry, getBeatGrid, getCaptions, getEditorSettings, getReframeTracks, getSegments, getStory,
  importClip, ingestUploaded, joinWithNext, listAudio, listClips, listStyles, listText, listVersions,
  patchEditorSettings, renderTimeline, reorderStory, replaceTimeline, restoreVersion, saveVersion, splitEntry,
  updateAudio, updateStoryEntry, updateText, uploadAudio,
} from "@/lib/api";
import { fmtTime, fx, itemAt, layout, sourceTimeAt, toRows } from "@/lib/timeline";
import PreviewPlayer from "./PreviewPlayer";
import Timeline, { Selection } from "./Timeline";
import MediaBin from "./MediaBin";
import ChatBox from "./ChatBox";
import CaptionsPanel from "./CaptionsPanel";
import AutoEditPanel from "./AutoEditPanel";
import { AudioInspector, ClipInspector, TextInspector } from "./Inspector";

interface Props {
  projectId: string;
  outputs: Output[];
  onRendered: () => void; // refetch outputs in the parent after a re-render
}

const SHORTCUTS: [string, string][] = [
  ["Space", "Play / pause"], ["← / →", "Step one frame (Shift: 1s)"], ["Home / End", "Start / end"],
  ["S", "Split at playhead"], ["I / O", "Set in / out of selected clip"], ["Del", "Delete selected"],
  ["Ctrl+D", "Duplicate clip"], ["Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"], ["T", "Add text at playhead"],
  ["+ / −", "Zoom timeline"],
];

export default function Editor({ projectId, outputs, onRendered }: Props) {
  const [entries, setEntries] = useState<StoryEntry[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [audio, setAudio] = useState<AudioTrack[]>([]);
  const [texts, setTexts] = useState<TextOverlay[]>([]);
  const [versions, setVersions] = useState<TimelineVersion[]>([]);
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rendering, setRendering] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);

  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [pxPerSec, setPxPerSec] = useState(60);
  const [aspect, setAspect] = useState<"16:9" | "9:16">("16:9");
  const [view, setView] = useState<"timeline" | "render">("timeline");
  const [side, setSide] = useState<"inspect" | "captions" | "chat">("inspect");
  const [panel, setPanel] = useState<"none" | "versions" | "style" | "keys" | "auto">("none");
  const [edSettings, setEdSettings] = useState<EditorSettings | null>(null);
  const [cues, setCues] = useState<CaptionCue[]>([]);
  const [capStatus, setCapStatus] = useState<TranscriptionStatus | null>(null);
  const [tracks, setTracks] = useState<Record<string, SubjectTrack>>({});
  const [beats, setBeats] = useState<number[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [versionLabel, setVersionLabel] = useState("");
  const [sourcePreview, setSourcePreview] = useState<{ url: string; at: number } | null>(null);

  const undoStack = useRef<TimelineRow[][]>([]);
  const redoStack = useRef<TimelineRow[][]>([]);
  const liveSnapshot = useRef<TimelineRow[] | null>(null);
  const [, forceHistory] = useState(0);

  const { items, total } = useMemo(() => layout(entries), [entries]);
  const selectedItem = selection?.kind === "clip" ? items.find((i) => i.entry.id === selection.id) ?? null : null;
  const selectedAudio = selection?.kind === "audio" ? audio.find((a) => a.id === selection.id) ?? null : null;
  const selectedText = selection?.kind === "text" ? texts.find((x) => x.id === selection.id) ?? null : null;
  const playheadItem = itemAt(items, t);

  const load = useCallback(async () => {
    try {
      const [s, seg, c, a, tx, v] = await Promise.all([
        getStory(projectId), getSegments(projectId), listClips(projectId),
        listAudio(projectId), listText(projectId), listVersions(projectId),
      ]);
      setEntries(s);
      setSegments(seg);
      setClips(c);
      setAudio(a);
      setTexts(tx);
      setVersions(v);
    } catch (e: any) {
      setError(e.message || "Failed to load the editor");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
    listStyles().then((all) => setStyles(all.filter((x) => x.status === "ready"))).catch(() => {});
    getEditorSettings(projectId).then(setEdSettings).catch(() => {});
  }, [load, projectId]);

  // Captions are derived from the timeline on the server, so refresh them after every change.
  const loadCaptions = useCallback(async () => {
    try {
      const c = await getCaptions(projectId);
      setCues(c.cues);
      setCapStatus(c.status);
    } catch {
      /* captions are optional */
    }
  }, [projectId]);
  useEffect(() => {
    const h = setTimeout(loadCaptions, 250);
    return () => clearTimeout(h);
  }, [entries, loadCaptions]);

  // Beat ticks for the first analysed music bed (they move with the bed and the edit length).
  const beatTrack = audio.find((a) => a.analysis?.bpm);
  useEffect(() => {
    if (!beatTrack) {
      setBeats([]);
      return;
    }
    getBeatGrid(projectId, beatTrack.id).then((g) => setBeats(g.beats_ms)).catch(() => setBeats([]));
  }, [projectId, beatTrack?.id, beatTrack?.start_ms, beatTrack?.source_offset_ms, beatTrack?.loop, entries]); // eslint-disable-line react-hooks/exhaustive-deps

  // Subject tracks drive smart framing in the vertical preview (computed once per clip on the server).
  useEffect(() => {
    if (aspect !== "9:16" || !entries.length) return;
    getReframeTracks(projectId).then(setTracks).catch(() => {});
  }, [aspect, projectId, clips.length, entries.length]);

  async function patchSettings(patch: { captions?: Partial<CaptionSettings>; reframe?: Partial<EditorSettings["reframe"]> }) {
    const r = await run(() => patchEditorSettings(projectId, patch));
    if (r) {
      setEdSettings(r);
      loadCaptions();
    }
  }

  async function detectBeats(track: AudioTrack) {
    setDetecting(true);
    try {
      const r = await analyzeBeats(projectId, track.id);
      setAudio(await listAudio(projectId));
      setNotice(`${Math.round(r.bpm)} BPM · ${r.beat_count} beats — ticks shown on the music lane`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDetecting(false);
    }
  }

  useEffect(() => {
    if (t > total) setT(total);
  }, [total, t]);

  // default the viewer aspect to the project's first output
  useEffect(() => {
    const o = outputs[0];
    if (o && o.height && o.width) setAspect(o.height > o.width ? "9:16" : "16:9");
  }, [outputs]);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e: any) {
      setError(e.message || "Action failed");
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  /** Every timeline mutation goes through here so it can be undone. */
  async function mutate(fn: () => Promise<StoryEntry[] | StoryEntry | void>, opts: { reload?: boolean; before?: TimelineRow[] } = {}) {
    const before = opts.before ?? toRows(entries);
    let ok = false;
    const result = await run(async () => {
      const r = await fn();
      ok = true; // distinguishes a successful void call from a failure (run() returns undefined for both)
      return r;
    });
    if (!ok) return false;
    undoStack.current.push(before);
    if (undoStack.current.length > 100) undoStack.current.shift();
    redoStack.current = [];
    forceHistory((n) => n + 1);
    if (Array.isArray(result)) setEntries(result);
    if (opts.reload) await load();
    return true;
  }

  async function undo() {
    const prev = undoStack.current.pop();
    if (!prev) return;
    const current = toRows(entries);
    const res = await run(() => replaceTimeline(projectId, prev));
    if (res) {
      redoStack.current.push(current);
      setEntries(res);
      setNotice("Undone");
    } else undoStack.current.push(prev);
    forceHistory((n) => n + 1);
  }

  async function redo() {
    const next = redoStack.current.pop();
    if (!next) return;
    const current = toRows(entries);
    const res = await run(() => replaceTimeline(projectId, next));
    if (res) {
      undoStack.current.push(current);
      setEntries(res);
      setNotice("Redone");
    } else redoStack.current.push(next);
    forceHistory((n) => n + 1);
  }

  // ── clip operations ─────────────────────────────────────────────────────────
  const patchLocal = (id: string, patch: Partial<StoryEntry>) =>
    setEntries((cur) => cur.map((e) => (e.id === id ? { ...e, ...patch } : e)));

  async function patchEntry(id: string, patch: Parameters<typeof updateStoryEntry>[2]) {
    await mutate(async () => {
      const r = await updateStoryEntry(projectId, id, patch);
      setEntries((cur) => cur.map((e) => (e.id === id ? { ...e, ...r, thumbnail_url: r.thumbnail_url ?? e.thumbnail_url } : e)));
    });
  }

  async function onEffects(id: string, patch: Effects, commit: boolean) {
    const entry = entries.find((e) => e.id === id);
    if (!entry) return;
    if (!liveSnapshot.current) liveSnapshot.current = toRows(entries);
    const merged = { ...fx(entry), ...patch };
    patchLocal(id, { effects: merged });
    if (!commit) return;
    const before = liveSnapshot.current;
    liveSnapshot.current = null;
    await mutate(async () => {
      const r = await updateStoryEntry(projectId, id, { effects: merged });
      patchLocal(id, { effects: r.effects });
    }, { before });
  }

  async function splitAtPlayhead() {
    const target = selectedItem && t > selectedItem.start && t < selectedItem.end ? selectedItem : playheadItem;
    if (!target) return;
    const at = sourceTimeAt(target, t);
    if (at - target.srcIn < 200 || target.srcOut - at < 200) {
      setNotice("Move the playhead inside a clip (away from its edges) to split");
      return;
    }
    await mutate(() => splitEntry(projectId, target.entry.id, at));
  }

  async function deleteSelected() {
    if (selectedItem) {
      const id = selectedItem.entry.id;
      setSelection(null);
      await mutate(() => deleteStoryEntry(projectId, id), { reload: true });
    } else if (selectedAudio) {
      setSelection(null);
      await run(() => deleteAudio(projectId, selectedAudio.id));
      setAudio((cur) => cur.filter((a) => a.id !== selectedAudio.id));
    } else if (selectedText) {
      setSelection(null);
      await run(() => deleteText(projectId, selectedText.id));
      setTexts((cur) => cur.filter((x) => x.id !== selectedText.id));
    }
  }

  async function setInOut(edge: "in" | "out") {
    if (!selectedItem || t < selectedItem.start || t > selectedItem.end) {
      setNotice("Select a clip and put the playhead inside it to set in/out");
      return;
    }
    const at = Math.round(sourceTimeAt(selectedItem, t));
    await patchEntry(selectedItem.entry.id, edge === "in" ? { trim_start_ms: at } : { trim_end_ms: at });
  }

  async function addTextAtPlayhead() {
    const start = Math.round(Math.min(t, Math.max(0, total - 500)));
    const created = await run(() => addText(projectId, {
      text: "Your text", start_ms: start, end_ms: start + 3000, position: "bottom", font_size: 56, color: "#ffffff", box: true,
    }));
    if (created) {
      setTexts((cur) => [...cur, created].sort((a, b) => a.start_ms - b.start_ms));
      setSelection({ kind: "text", id: created.id });
      setSide("inspect");
    }
  }

  // ── media ───────────────────────────────────────────────────────────────────
  async function importVideos(files: File[]) {
    setError("");
    try {
      for (let i = 0; i < files.length; i++) {
        setImporting(`Importing ${files[i].name} (${i + 1}/${files.length}) — detecting scenes…`);
        await importClip(projectId, files[i]);
      }
      await load();
      setNotice(`${files.length} clip(s) imported`);
    } catch (e: any) {
      setError(e.message || "Import failed");
    } finally {
      setImporting(null);
    }
  }

  async function importSong(file: File) {
    setImporting(`Uploading ${file.name}…`);
    setError("");
    try {
      const a = await uploadAudio(projectId, file);
      setAudio((cur) => [...cur, a]);
      setSelection({ kind: "audio", id: a.id });
      setSide("inspect");
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setImporting(null);
    }
  }

  async function prepareUploaded() {
    setImporting("Preparing uploaded clips — probing & detecting scenes…");
    try {
      await ingestUploaded(projectId);
      await load();
    } catch (e: any) {
      setError(e.message || "Failed");
    } finally {
      setImporting(null);
    }
  }

  // ── render / versions / style ─────────────────────────────────────────────
  async function handleRender() {
    setRendering(true);
    setError("");
    setNotice("");
    try {
      const r = await renderTimeline(projectId);
      onRendered();
      setView("render");
      setNotice(r.any_failed ? "Some formats failed to render" : r.warnings?.length ? `Rendered with notes: ${r.warnings.join("; ")}`
        : `Rendered · ${fmtTime(r.total_duration_ms)}`);
    } catch (e: any) {
      setError(e.message || "Render failed");
    } finally {
      setRendering(false);
    }
  }

  async function doSaveVersion() {
    const label = versionLabel.trim() || `Version ${new Date().toLocaleTimeString()}`;
    const v = await run(() => saveVersion(projectId, label));
    if (v) {
      setVersions((cur) => [v, ...cur]);
      setVersionLabel("");
      setNotice(`Saved “${v.label}”`);
    }
  }

  async function doRestore(v: TimelineVersion) {
    const ok = await mutate(() => restoreVersion(projectId, v.id));
    if (ok) {
      setNotice(`Restored “${v.label}” (current cut auto-saved)`);
      listVersions(projectId).then(setVersions).catch(() => {});
    }
  }

  async function doApplyStyle(p: StyleProfile) {
    setPanel("none");
    const ok = await mutate(() => applyStyle(projectId, p.id), { reload: true });
    if (ok) {
      setNotice(`Re-cut in “${p.name}” style — the previous cut was saved as a version`);
      setT(0);
      listVersions(projectId).then(setVersions).catch(() => {});
    }
  }

  // ── keyboard shortcuts ────────────────────────────────────────────────────
  const keyHandler = useRef<(e: KeyboardEvent) => void>(() => {});
  keyHandler.current = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (k === " ") { e.preventDefault(); setView("timeline"); setPlaying((p) => !p); }
    else if (mod && k === "z" && e.shiftKey) { e.preventDefault(); redo(); }
    else if (mod && k === "z") { e.preventDefault(); undo(); }
    else if (mod && k === "y") { e.preventDefault(); redo(); }
    else if (mod && k === "d") { e.preventDefault(); if (selectedItem) mutate(() => duplicateEntry(projectId, selectedItem.entry.id)); }
    else if (mod) return;
    else if (k === "arrowleft") { e.preventDefault(); setPlaying(false); setT((x) => Math.max(0, x - (e.shiftKey ? 1000 : 1000 / 30))); }
    else if (k === "arrowright") { e.preventDefault(); setPlaying(false); setT((x) => Math.min(total, x + (e.shiftKey ? 1000 : 1000 / 30))); }
    else if (k === "home") { e.preventDefault(); setT(0); }
    else if (k === "end") { e.preventDefault(); setT(total); }
    else if (k === "s" || k === "b") { e.preventDefault(); setPlaying(false); splitAtPlayhead(); }
    else if (k === "i") setInOut("in");
    else if (k === "o") setInOut("out");
    else if (k === "t") { e.preventDefault(); addTextAtPlayhead(); }
    else if (k === "delete" || k === "backspace") { e.preventDefault(); deleteSelected(); }
    else if (k === "+" || k === "=") setPxPerSec((z) => Math.min(300, Math.round(z * 1.25)));
    else if (k === "-" || k === "_") setPxPerSec((z) => Math.max(10, Math.round(z / 1.25)));
    else if (k === "escape") setSelection(null);
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyHandler.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(id);
  }, [notice]);

  const renderedOutput =
    outputs.find((o) => o.download_url && (aspect === "9:16" ? (o.height ?? 0) > (o.width ?? 0) : (o.width ?? 0) >= (o.height ?? 0)))
    ?? outputs.find((o) => o.download_url);

  if (loading) return <div className="py-10 text-center text-sm text-slate-400">Loading editor…</div>;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      {/* relative z-30: backdrop-blur makes the toolbar a stacking context, so its popovers
          could otherwise never paint above the panels below it */}
      <div className="glass relative z-30 flex flex-wrap items-center gap-1.5 rounded-2xl px-2.5 py-2 text-[11px]">
        <Tool onClick={undo} disabled={busy || !undoStack.current.length} title="Undo (Ctrl+Z)">↶ Undo</Tool>
        <Tool onClick={redo} disabled={busy || !redoStack.current.length} title="Redo (Ctrl+Shift+Z)">↷ Redo</Tool>
        <Sep />
        <Tool onClick={splitAtPlayhead} disabled={busy || !items.length} title="Split at playhead (S)">✂ Split</Tool>
        <Tool onClick={() => selectedItem && mutate(() => duplicateEntry(projectId, selectedItem.entry.id))} disabled={busy || !selectedItem} title="Duplicate (Ctrl+D)">⧉ Duplicate</Tool>
        <Tool onClick={() => selectedItem && mutate(() => joinWithNext(projectId, selectedItem.entry.id))} disabled={busy || !selectedItem} title="Stitch with next clip">🔗 Join</Tool>
        <Tool onClick={deleteSelected} disabled={busy || !selection} title="Delete (Del)">🗑 Delete</Tool>
        <Tool onClick={addTextAtPlayhead} disabled={busy} title="Add text at playhead (T)">🔤 Text</Tool>
        <Sep />
        <Tool onClick={() => setPxPerSec((z) => Math.max(10, Math.round(z / 1.25)))} title="Zoom out (−)">−</Tool>
        <span className="w-10 text-center font-mono text-slate-500">{pxPerSec}px/s</span>
        <Tool onClick={() => setPxPerSec((z) => Math.min(300, Math.round(z * 1.25)))} title="Zoom in (+)">＋</Tool>
        <Sep />
        <div className="relative">
          <Tool onClick={() => setPanel(panel === "versions" ? "none" : "versions")} title="Save / restore versions">🕘 Versions ({versions.length})</Tool>
          {panel === "versions" && (
            <Popover onClose={() => setPanel("none")}>
              <div className="mb-2 flex gap-1">
                <input value={versionLabel} onChange={(e) => setVersionLabel(e.target.value)} placeholder="Version name"
                  onKeyDown={(e) => e.key === "Enter" && doSaveVersion()}
                  className="flex-1 rounded border border-border bg-surface2 px-2 py-1 text-slate-100 outline-none focus:border-accent" />
                <button className="rounded bg-accent px-2 text-white disabled:opacity-50" disabled={busy} onClick={doSaveVersion}>Save</button>
              </div>
              {versions.length === 0 ? <p className="text-slate-500">No versions yet.</p> : (
                <ul className="max-h-64 space-y-1 overflow-y-auto">
                  {versions.map((v) => (
                    <li key={v.id} className="flex items-center gap-1 rounded border border-border bg-surface2 px-2 py-1">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-slate-200">{v.label}</p>
                        <p className="text-[9px] text-slate-500">{v.entry_count} clips · {new Date(v.created_at).toLocaleString()}</p>
                      </div>
                      <button className="text-accent hover:underline" disabled={busy} onClick={() => doRestore(v)}>restore</button>
                      <button className="text-slate-500 hover:text-red-300" aria-label="Delete version"
                        onClick={() => run(() => deleteVersion(projectId, v.id)).then(() => setVersions((c) => c.filter((x) => x.id !== v.id)))}>✕</button>
                    </li>
                  ))}
                </ul>
              )}
            </Popover>
          )}
        </div>
        <div className="relative">
          <Tool onClick={() => setPanel(panel === "style" ? "none" : "style")} title="Re-cut this footage in a learned editor style">🎓 Apply style</Tool>
          {panel === "style" && (
            <Popover onClose={() => setPanel("none")}>
              {styles.length === 0 ? (
                <p className="text-slate-400">No trained styles yet. <a href="/styles" className="text-accent underline">Teach the AI your style →</a></p>
              ) : (
                <ul className="space-y-1">
                  <li className="pb-1 text-[10px] text-slate-500">Rebuilds the timeline from all imported footage, the way that editor would cut it. Your current cut is saved as a version first.</li>
                  {styles.map((s) => (
                    <li key={s.id}>
                      <button disabled={busy} onClick={() => doApplyStyle(s)}
                        className="w-full rounded border border-border bg-surface2 px-2 py-1.5 text-left hover:border-accent disabled:opacity-50">
                        <span className="font-semibold text-slate-100">{s.name}</span>
                        <span className="block truncate text-[10px] text-slate-500">{s.summary?.split("\n")[0]}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Popover>
          )}
        </div>
        <div className="relative">
          <Tool onClick={() => setPanel(panel === "auto" ? "none" : "auto")} title="Remove silences, sync to the beat, framing">✨ Auto-edit</Tool>
          {panel === "auto" && (
            <Popover onClose={() => setPanel("none")} wide>
              <AutoEditPanel
                projectId={projectId} audio={audio} settings={edSettings} busy={busy}
                onApply={async (fn, msg) => {
                  const ok = await mutate(fn);
                  if (ok) {
                    setNotice(msg);
                    listVersions(projectId).then(setVersions).catch(() => {});
                  }
                }}
                onFraming={(mode) => patchSettings({ reframe: { mode } })}
              />
            </Popover>
          )}
        </div>
        <div className="relative">
          <Tool onClick={() => setPanel(panel === "keys" ? "none" : "keys")} title="Keyboard shortcuts">⌨</Tool>
          {panel === "keys" && (
            <Popover onClose={() => setPanel("none")}>
              <table className="w-full">
                <tbody>
                  {SHORTCUTS.map(([k, d]) => (
                    <tr key={k}><td className="pr-3 font-mono text-accent">{k}</td><td className="text-slate-300">{d}</td></tr>
                  ))}
                </tbody>
              </table>
            </Popover>
          )}
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Seg value={aspect} options={[["16:9", "16:9"], ["9:16", "9:16"]]} onChange={(v) => setAspect(v as "16:9" | "9:16")} />
          <Seg value={view} options={[["timeline", "Live"], ["render", "Rendered"]]} onChange={(v) => { setPlaying(false); setView(v as "timeline" | "render"); }} />
          <button onClick={handleRender} disabled={rendering || busy || !entries.length}
            className="rounded-lg bg-gradient-to-r from-accent to-accent4 px-3 py-1.5 text-[11px] font-semibold text-white transition hover:opacity-90 disabled:opacity-50">
            {rendering ? "Rendering…" : "🎬 Render"}
          </button>
        </div>
      </div>

      {(error || notice) && (
        <div className={`rounded-lg border px-3 py-1.5 text-xs ${error ? "border-red-500/30 bg-red-500/10 text-red-300" : "border-accent/30 bg-accent/10 text-slate-200"}`}>
          {error || notice}
        </div>
      )}

      {/* Bin | Viewer | Inspector */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[300px_1fr_300px]">
        <div className="h-[420px] xl:order-1">
          <MediaBin
            segments={segments} clips={clips} audio={audio} busy={busy} importing={importing}
            onAddSegment={(id) => mutate(() => addStoryEntry(projectId, id), { reload: true })}
            onAddClip={(id) => mutate(() => addClipRange(projectId, id), { reload: true })}
            onImportVideos={importVideos} onImportAudio={importSong} onPrepare={prepareUploaded}
            onPreview={(url, at) => { setPlaying(false); setSourcePreview({ url, at }); }}
          />
        </div>

        <div className="glass relative h-[420px] rounded-2xl p-2 xl:order-2">
          {sourcePreview ? (
            <div className="flex h-full flex-col">
              <video src={`${sourcePreview.url}#t=${sourcePreview.at / 1000}`} controls autoPlay className="min-h-0 flex-1 rounded-xl bg-black" />
              <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
                <span>Source preview</span>
                <button className="btn-ghost !py-1" onClick={() => setSourcePreview(null)}>Back to timeline</button>
              </div>
            </div>
          ) : view === "render" ? (
            <div className="flex h-full flex-col">
              {renderedOutput?.download_url ? (
                <video key={renderedOutput.download_url} src={renderedOutput.download_url} controls className="min-h-0 flex-1 rounded-xl bg-black" />
              ) : (
                <div className="flex flex-1 items-center justify-center rounded-xl bg-black/50 text-xs text-slate-500">No render yet — hit 🎬 Render.</div>
              )}
              <p className="mt-2 text-[11px] text-slate-400">
                Last render{renderedOutput ? ` · ${renderedOutput.format} · ${renderedOutput.aspect_ratio}` : ""} — includes transitions, music, text & loudness.
              </p>
            </div>
          ) : (
            <PreviewPlayer items={items} total={total} t={t} playing={playing}
              onTime={setT} onPlayingChange={setPlaying} audio={audio} texts={texts} aspect={aspect}
              cues={cues} captions={edSettings?.captions} tracks={tracks} reframeMode={edSettings?.reframe.mode} />
          )}
        </div>

        <div className="glass flex h-[420px] flex-col rounded-2xl xl:order-3">
          <div className="flex gap-1 border-b border-border p-1.5">
            {(["inspect", "captions", "chat"] as const).map((s) => (
              <button key={s} onClick={() => setSide(s)}
                className={`rounded-lg px-2 py-1 text-[11px] font-medium ${side === s ? "bg-accent/25 text-white" : "text-slate-400 hover:text-white"}`}>
                {s === "inspect" ? "🎛 Inspector" : s === "captions" ? "💬 Captions" : "🤖 AI chat"}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {side === "chat" ? (
              <ChatBox projectId={projectId} onApplied={() => {
                load();
                getEditorSettings(projectId).then(setEdSettings).catch(() => {});
                listVersions(projectId).then(setVersions).catch(() => {});
              }} />
            ) : side === "captions" ? (
              <CaptionsPanel projectId={projectId} cues={cues} status={capStatus} settings={edSettings?.captions ?? null} t={t}
                onSettings={(patch) => patchSettings({ captions: patch })} onReload={loadCaptions}
                onSeek={(x) => { setPlaying(false); setView("timeline"); setSourcePreview(null); setT(x); }} />
            ) : selectedItem ? (
              <ClipInspector
                item={selectedItem} busy={busy}
                playheadSrcMs={t >= selectedItem.start && t <= selectedItem.end ? sourceTimeAt(selectedItem, t) : null}
                onEffects={(patch, commit) => onEffects(selectedItem.entry.id, patch, commit)}
                onPatch={(patch) => patchEntry(selectedItem.entry.id, patch as any)}
                onSplit={splitAtPlayhead}
                onDuplicate={() => mutate(() => duplicateEntry(projectId, selectedItem.entry.id))}
                onJoin={() => mutate(() => joinWithNext(projectId, selectedItem.entry.id))}
                onDelete={deleteSelected}
                projectFraming={edSettings?.reframe.mode ?? "smart"}
                onReframe={(rp) => patchEntry(selectedItem.entry.id, { reframe_params: rp })}
              />
            ) : selectedAudio ? (
              <AudioInspector track={selectedAudio} total={total} onDelete={deleteSelected}
                onDetectBeats={() => detectBeats(selectedAudio)} detecting={detecting}
                onPatch={async (patch) => {
                  const r = await run(() => updateAudio(projectId, selectedAudio.id, patch));
                  if (r) setAudio((cur) => cur.map((a) => (a.id === r.id ? r : a)));
                }} />
            ) : selectedText ? (
              <TextInspector overlay={selectedText} onDelete={deleteSelected}
                onPatch={async (patch) => {
                  const r = await run(() => updateText(projectId, selectedText.id, patch));
                  if (r) setTexts((cur) => cur.map((x) => (x.id === r.id ? r : x)));
                }} />
            ) : (
              <div className="space-y-2 text-[11px] text-slate-500">
                <p className="text-slate-300">Select a clip, song or text on the timeline to edit it.</p>
                <p>Drag clip edges to trim · drag clips to reorder · drag music/text bars to move them.</p>
                <p>Press <span className="font-mono text-accent">⌨</span> in the toolbar for keyboard shortcuts.</p>
              </div>
            )}
          </div>
        </div>
      </div>

      <Timeline
        items={items} total={total} t={t} pxPerSec={pxPerSec} selection={selection} busy={busy}
        audio={audio} texts={texts} beats={beats}
        onSeek={(x) => { setPlaying(false); setView("timeline"); setSourcePreview(null); setT(x); }}
        onSelect={(s) => { setSelection(s); setSide("inspect"); }}
        onReorder={(ids) => {
          const prev = entries;
          setEntries(ids.map((id) => prev.find((e) => e.id === id)!).filter(Boolean));
          mutate(() => reorderStory(projectId, ids), { before: toRows(prev) }).then((ok) => { if (!ok) setEntries(prev); });
        }}
        onTrim={(id, patch) => patchEntry(id, patch)}
        onMoveAudio={async (id, start) => {
          setAudio((cur) => cur.map((a) => (a.id === id ? { ...a, start_ms: start } : a)));
          const r = await run(() => updateAudio(projectId, id, { start_ms: start }));
          if (r) setAudio((cur) => cur.map((a) => (a.id === r.id ? r : a)));
        }}
        onMoveText={async (id, s, e) => {
          setTexts((cur) => cur.map((x) => (x.id === id ? { ...x, start_ms: s, end_ms: e } : x)));
          const r = await run(() => updateText(projectId, id, { start_ms: s, end_ms: e }));
          if (r) setTexts((cur) => cur.map((x) => (x.id === r.id ? r : x)));
        }}
      />
    </div>
  );
}

function Tool({ children, onClick, disabled, title }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; title?: string }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-slate-200 transition hover:border-accent/60 hover:bg-accent/10 disabled:opacity-35">
      {children}
    </button>
  );
}

function Sep() {
  return <span className="mx-0.5 h-5 w-px bg-white/10" />;
}

function Seg({ value, options, onChange }: { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="flex rounded-lg border border-white/10 bg-black/30 p-0.5">
      {options.map(([v, label]) => (
        <button key={v} onClick={() => onChange(v)}
          className={`rounded-md px-2 py-0.5 ${value === v ? "bg-accent/30 text-white" : "text-slate-400 hover:text-white"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

function Popover({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div className={`absolute left-0 top-full z-40 mt-1 max-h-[70vh] overflow-y-auto rounded-xl border border-white/10 bg-surface p-2.5 text-[11px] shadow-2xl ${wide ? "w-96" : "w-72"}`}>
        {children}
      </div>
    </>
  );
}
