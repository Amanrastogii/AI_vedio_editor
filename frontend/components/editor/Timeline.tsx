"use client";

import { useRef, useState } from "react";
import {
  DndContext, DragEndEvent, PointerSensor, closestCenter, useSensor, useSensors,
} from "@dnd-kit/core";
import {
  SortableContext, arrayMove, horizontalListSortingStrategy, useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { AudioTrack, TextOverlay } from "@/lib/api";
import { LaidOut, fmtTime, fx } from "@/lib/timeline";

export type Selection =
  | { kind: "clip"; id: string }
  | { kind: "audio"; id: string }
  | { kind: "text"; id: string }
  | null;

interface Props {
  items: LaidOut[];
  total: number;
  t: number;
  pxPerSec: number;
  selection: Selection;
  busy?: boolean;
  audio: AudioTrack[];
  texts: TextOverlay[];
  beats?: number[]; // sequence-ms beat grid of the analysed music bed (drawn as ticks)
  onSeek: (t: number) => void;
  onSelect: (s: Selection) => void;
  onReorder: (ids: string[]) => void;
  onTrim: (entryId: string, patch: { trim_start_ms?: number; trim_end_ms?: number }) => void;
  onMoveAudio: (id: string, startMs: number) => void;
  onMoveText: (id: string, startMs: number, endMs: number) => void;
}

const LABEL_W = 64;
const MIN_MS = 200;

export default function Timeline(p: Props) {
  const { items, total, t, pxPerSec, selection } = p;
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const px = (ms: number) => (ms / 1000) * pxPerSec;
  const width = Math.max(px(total) + 240, 600);
  const rulerRef = useRef<HTMLDivElement>(null);

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const ids = items.map((x) => x.entry.id);
    p.onReorder(arrayMove(ids, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))));
  }

  function seekFromEvent(clientX: number) {
    const el = rulerRef.current;
    if (!el) return;
    const x = clientX - el.getBoundingClientRect().left;
    p.onSeek(Math.max(0, Math.min(total, (x / pxPerSec) * 1000)));
  }

  // tick spacing adapts to zoom
  const step = pxPerSec >= 120 ? 1 : pxPerSec >= 50 ? 2 : pxPerSec >= 20 ? 5 : 10;
  const ticks = Array.from({ length: Math.ceil(width / pxPerSec / step) + 1 }, (_, i) => i * step);

  return (
    <div className="glass overflow-hidden rounded-2xl">
      <div className="relative overflow-x-auto">
        <div className="relative" style={{ width: width + LABEL_W }}>
          {/* Ruler */}
          <div className="flex h-7 border-b border-border">
            <div className="sticky left-0 z-20 w-16 shrink-0 bg-surface" />
            <div
              ref={rulerRef}
              className="relative flex-1 cursor-pointer select-none"
              onPointerDown={(e) => {
                (e.target as HTMLElement).setPointerCapture(e.pointerId);
                seekFromEvent(e.clientX);
              }}
              onPointerMove={(e) => e.buttons === 1 && seekFromEvent(e.clientX)}
            >
              {ticks.map((s) => (
                <div key={s} className="absolute top-0 h-full border-l border-white/10" style={{ left: px(s * 1000) }}>
                  <span className="ml-1 font-mono text-[9px] text-slate-500">{fmtTime(s * 1000)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Video track */}
          <Lane label="🎬 Video" height={84}>
            {items.length === 0 ? (
              <div className="flex h-full items-center px-3 text-[11px] text-slate-500">
                Empty — add clips from the media bin (or apply a learned style).
              </div>
            ) : (
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={items.map((x) => x.entry.id)} strategy={horizontalListSortingStrategy}>
                  <div className="flex h-full items-stretch py-1.5">
                    {items.map((it, i) => {
                      const nextOverlap = items[i + 1]?.overlapIn ?? 0;
                      return (
                        <ClipBlock
                          key={it.entry.id}
                          item={it}
                          widthPx={Math.max(8, px(it.duration - nextOverlap))}
                          pxPerSec={pxPerSec}
                          selected={selection?.kind === "clip" && selection.id === it.entry.id}
                          disabled={p.busy}
                          onSelect={() => p.onSelect({ kind: "clip", id: it.entry.id })}
                          onTrim={p.onTrim}
                        />
                      );
                    })}
                  </div>
                </SortableContext>
              </DndContext>
            )}
          </Lane>

          {/* Text track */}
          <Lane label="🔤 Text" height={30}>
            {p.texts.map((x) => (
              <DragBar
                key={x.id}
                left={px(x.start_ms)}
                width={px(x.end_ms - x.start_ms)}
                pxPerSec={pxPerSec}
                selected={selection?.kind === "text" && selection.id === x.id}
                className="bg-accent3/25 border-accent3/60 text-amber-100"
                onClick={() => p.onSelect({ kind: "text", id: x.id })}
                onMove={(dms) => {
                  const s = Math.max(0, x.start_ms + dms);
                  p.onMoveText(x.id, s, s + (x.end_ms - x.start_ms));
                }}
              >
                {x.text}
              </DragBar>
            ))}
          </Lane>

          {/* Music track */}
          <Lane label="🎵 Music" height={30}>
            {(p.beats || []).map((b) => (
              <div key={b} className="pointer-events-none absolute bottom-0 z-10 h-2 w-px bg-accent2/80" style={{ left: px(b) }} />
            ))}
            {p.audio.map((a) => {
              const len = a.length_ms ?? Math.max(0, total - a.start_ms);
              return (
                <DragBar
                  key={a.id}
                  left={px(a.start_ms)}
                  width={px(len)}
                  pxPerSec={pxPerSec}
                  selected={selection?.kind === "audio" && selection.id === a.id}
                  className="bg-accent2/20 border-accent2/60 text-emerald-100"
                  onClick={() => p.onSelect({ kind: "audio", id: a.id })}
                  onMove={(dms) => p.onMoveAudio(a.id, Math.max(0, a.start_ms + dms))}
                >
                  {a.loop ? "🔁 " : ""}
                  {a.original_filename}
                  {a.analysis?.bpm ? <span className="ml-1 text-emerald-300/80">· {Math.round(a.analysis.bpm)} BPM</span> : null}
                </DragBar>
              );
            })}
          </Lane>

          {/* Playhead */}
          <div
            className="pointer-events-none absolute bottom-0 top-0 z-30 w-px bg-accent4 shadow-[0_0_8px_rgba(233,107,140,0.9)]"
            style={{ left: LABEL_W + px(t) }}
          >
            <div className="absolute -left-1.5 top-0 h-3 w-3 rotate-45 bg-accent4" />
          </div>
        </div>
      </div>
    </div>
  );
}

function Lane({ label, height, children }: { label: string; height: number; children: React.ReactNode }) {
  return (
    <div className="flex border-b border-border/60" style={{ height }}>
      <div className="sticky left-0 z-20 flex w-16 shrink-0 items-center border-r border-border bg-surface px-2 text-[10px] font-semibold text-slate-400">
        {label}
      </div>
      <div className="relative flex-1">{children}</div>
    </div>
  );
}

function ClipBlock({
  item, widthPx, pxPerSec, selected, disabled, onSelect, onTrim,
}: {
  item: LaidOut;
  widthPx: number;
  pxPerSec: number;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
  onTrim: Props["onTrim"];
}) {
  const e = item.entry;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: e.id, disabled });
  const [drag, setDrag] = useState<{ edge: "in" | "out"; startX: number; delta: number } | null>(null);
  const segStart = e.start_ms ?? 0;
  const segEnd = e.end_ms ?? item.srcOut;

  // Live width while trimming (source ms → sequence px)
  const deltaSeqPx = drag ? (drag.delta / item.speed / 1000) * pxPerSec : 0;
  const shownWidth = Math.max(8, widthPx + (drag?.edge === "in" ? -deltaSeqPx : deltaSeqPx));

  function clampDelta(edge: "in" | "out", dSrc: number) {
    if (edge === "in") return Math.max(segStart - item.srcIn, Math.min(item.srcOut - MIN_MS - item.srcIn, dSrc));
    return Math.max(item.srcIn + MIN_MS - item.srcOut, Math.min(segEnd - item.srcOut, dSrc));
  }

  function handle(edge: "in" | "out") {
    return {
      onPointerDown: (ev: React.PointerEvent) => {
        if (disabled) return;
        ev.stopPropagation();
        (ev.target as HTMLElement).setPointerCapture(ev.pointerId);
        setDrag({ edge, startX: ev.clientX, delta: 0 });
      },
      onPointerMove: (ev: React.PointerEvent) => {
        if (!drag) return;
        const dSrc = ((ev.clientX - drag.startX) / pxPerSec) * 1000 * item.speed;
        setDrag({ ...drag, delta: clampDelta(edge, dSrc) });
      },
      onPointerUp: () => {
        if (drag && Math.abs(drag.delta) > 20) {
          onTrim(e.id, edge === "in"
            ? { trim_start_ms: Math.round(item.srcIn + drag.delta) }
            : { trim_end_ms: Math.round(item.srcOut + drag.delta) });
        }
        setDrag(null);
      },
    };
  }

  const effects = fx(e);
  const badges = [
    effects.speed !== 1 ? `${effects.speed}x` : "",
    effects.muted ? "🔇" : "",
    effects.filter !== "none" ? effects.filter : "",
  ].filter(Boolean);

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, width: shownWidth, opacity: isDragging ? 0.5 : 1 }}
      className={`group relative h-full shrink-0 overflow-hidden rounded-md border ${
        selected ? "z-10 border-accent4 ring-2 ring-accent4/60" : "border-white/15"
      } bg-surface2`}
      onClick={(ev) => {
        ev.stopPropagation();
        onSelect();
      }}
    >
      <div {...attributes} {...listeners} className="absolute inset-0 cursor-grab active:cursor-grabbing">
        {e.thumbnail_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={e.thumbnail_url} alt="" className="h-full w-full object-cover opacity-60" draggable={false} />
        )}
        <div className="absolute inset-x-0 top-0 flex items-center gap-1 truncate bg-gradient-to-b from-black/80 to-transparent px-1.5 py-0.5 text-[10px] font-semibold text-white">
          <span>{item.index + 1}</span>
          <span className="truncate font-normal text-slate-300">{e.narrative_role.replace("_", " ")}</span>
        </div>
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-0.5 text-[9px] text-slate-200">
          <span className="font-mono">{((item.duration + (drag ? (drag.edge === "in" ? -drag.delta : drag.delta) / item.speed : 0)) / 1000).toFixed(1)}s</span>
          <span className="truncate">{badges.join(" · ")}</span>
        </div>
      </div>
      {item.index > 0 && e.transition_in !== "cut" && (
        <div
          className="pointer-events-none absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-r bg-accent/80 px-1 text-[8px] font-bold text-white"
          title={`Transition: ${e.transition_in}`}
        >
          ⧓
        </div>
      )}
      {/* trim handles */}
      <div {...handle("in")} className="absolute bottom-0 left-0 top-0 z-20 w-2 cursor-ew-resize bg-white/0 hover:bg-accent4/70" title="Drag to trim start" />
      <div {...handle("out")} className="absolute bottom-0 right-0 top-0 z-20 w-2 cursor-ew-resize bg-white/0 hover:bg-accent4/70" title="Drag to trim end" />
    </div>
  );
}

function DragBar({
  left, width, pxPerSec, selected, className, onClick, onMove, children,
}: {
  left: number;
  width: number;
  pxPerSec: number;
  selected: boolean;
  className: string;
  onClick: () => void;
  onMove: (deltaMs: number) => void;
  children: React.ReactNode;
}) {
  const [drag, setDrag] = useState<{ startX: number; dx: number } | null>(null);
  return (
    <div
      className={`absolute top-1 bottom-1 cursor-grab touch-none select-none truncate rounded border px-1.5 text-[10px] leading-[20px] active:cursor-grabbing ${className} ${
        selected ? "ring-2 ring-accent4/70" : ""
      }`}
      style={{ left: left + (drag?.dx ?? 0), width: Math.max(10, width) }}
      onPointerDown={(e) => {
        e.stopPropagation();
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
        setDrag({ startX: e.clientX, dx: 0 });
        onClick();
      }}
      onPointerMove={(e) => drag && setDrag({ ...drag, dx: e.clientX - drag.startX })}
      onPointerUp={() => {
        if (drag && Math.abs(drag.dx) > 3) onMove(Math.round((drag.dx / pxPerSec) * 1000));
        setDrag(null);
      }}
    >
      {children}
    </div>
  );
}
