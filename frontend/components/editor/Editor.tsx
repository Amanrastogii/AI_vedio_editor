"use client";

import { useEffect, useState } from "react";
import {
  Output, Segment, StoryEntry,
  addStoryEntry, deleteStoryEntry, getSegments, getStory,
  renderTimeline, reorderStory, updateStoryEntry,
} from "@/lib/api";
import VideoPlayer from "./VideoPlayer";
import Timeline from "./Timeline";
import ClipBin from "./ClipBin";
import ChatBox from "./ChatBox";

interface Props {
  projectId: string;
  outputs: Output[];
  onRendered: () => void; // refetch outputs in the parent after a re-render
}

export default function Editor({ projectId, outputs, onRendered }: Props) {
  const [entries, setEntries] = useState<StoryEntry[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(false);

  async function load() {
    try {
      const [s, seg] = await Promise.all([getStory(projectId), getSegments(projectId)]);
      setEntries(s);
      setSegments(seg);
    } catch (e: any) {
      setError(e.message || "Failed to load timeline");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function withBusy<T>(fn: () => Promise<T>) {
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

  async function handleReorder(ids: string[]) {
    // Optimistic reorder keeps each entry's already-resolved thumbnail_url;
    // the server response (position numbers only) would otherwise overwrite
    // it with null, so only fall back to it on failure.
    const prev = entries;
    setEntries((cur) => ids.map((id) => cur.find((e) => e.id === id)!).filter(Boolean));
    const result = await withBusy(() => reorderStory(projectId, ids));
    if (!result) setEntries(prev);
  }

  // PATCH /story/{id} doesn't re-resolve the thumbnail URL, so merge only the
  // fields that actually changed instead of replacing the whole entry —
  // otherwise the clip thumbnail would disappear after a trim/transition edit.
  async function handleTrim(entryId: string, patch: { trim_start_ms?: number; trim_end_ms?: number }) {
    const result = await withBusy(() => updateStoryEntry(projectId, entryId, patch));
    if (result) setEntries((cur) => cur.map((e) => (e.id === entryId ? { ...e, ...patch } : e)));
  }

  async function handleTransition(entryId: string, value: string) {
    await withBusy(() => updateStoryEntry(projectId, entryId, { transition_in: value }));
    setEntries((cur) => cur.map((e) => (e.id === entryId ? { ...e, transition_in: value } : e)));
  }

  async function handleRemove(entryId: string) {
    await withBusy(() => deleteStoryEntry(projectId, entryId));
    await load();
  }

  async function handleAdd(segmentId: string) {
    await withBusy(() => addStoryEntry(projectId, segmentId));
    await load();
  }

  async function handleRerender() {
    setRendering(true);
    setError("");
    try {
      await renderTimeline(projectId);
      onRendered();
    } catch (e: any) {
      setError(e.message || "Render failed");
    } finally {
      setRendering(false);
    }
  }

  const previewOutput = outputs[0];

  if (loading) {
    return <div className="py-10 text-center text-sm text-slate-400">Loading editor…</div>;
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
        <VideoPlayer
          src={previewOutput?.download_url || null}
          label={previewOutput ? `Preview · ${previewOutput.format} · last rendered` : undefined}
        />
        <div style={{ height: 320 }}>
          <ChatBox projectId={projectId} onApplied={load} />
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-300">Timeline</h3>
          <button
            onClick={handleRerender}
            disabled={rendering || busy || entries.length === 0}
            className="rounded-lg bg-gradient-to-r from-accent to-accent4 px-4 py-1.5 text-xs font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {rendering ? "Rendering…" : "🎬 Re-render"}
          </button>
        </div>
        <Timeline
          entries={entries}
          busy={busy}
          onReorder={handleReorder}
          onTrim={handleTrim}
          onTransition={handleTransition}
          onRemove={handleRemove}
        />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-bold uppercase tracking-wider text-slate-300">Clip bin</h3>
        <ClipBin segments={segments} busy={busy} onAdd={handleAdd} />
      </div>
    </div>
  );
}
