"use client";

import { useState } from "react";
import {
  DndContext, DragEndEvent, PointerSensor, closestCenter, useSensor, useSensors,
} from "@dnd-kit/core";
import {
  SortableContext, arrayMove, horizontalListSortingStrategy, useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { StoryEntry } from "@/lib/api";

const TRANSITIONS = ["cut", "dissolve", "fade_to_black", "wipe", "zoom_in", "zoom_out", "cross_fade"];

interface Props {
  entries: StoryEntry[];
  onReorder: (ids: string[]) => void;
  onTrim: (entryId: string, patch: { trim_start_ms?: number; trim_end_ms?: number }) => void;
  onTransition: (entryId: string, value: string) => void;
  onRemove: (entryId: string) => void;
  busy?: boolean;
}

export default function Timeline({ entries, onReorder, onTrim, onTransition, onRemove, busy }: Props) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIndex = entries.findIndex((x) => x.id === active.id);
    const newIndex = entries.findIndex((x) => x.id === over.id);
    onReorder(arrayMove(entries, oldIndex, newIndex).map((x) => x.id));
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-surface/50 py-10 text-center text-sm text-slate-400">
        Timeline is empty — add clips from the bin below.
      </div>
    );
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={entries.map((e) => e.id)} strategy={horizontalListSortingStrategy}>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {entries.map((entry, i) => (
            <TimelineCard
              key={entry.id}
              entry={entry}
              index={i}
              disabled={busy}
              onTrim={onTrim}
              onTransition={onTransition}
              onRemove={onRemove}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

function TimelineCard({
  entry, index, disabled, onTrim, onTransition, onRemove,
}: {
  entry: StoryEntry;
  index: number;
  disabled?: boolean;
  onTrim: Props["onTrim"];
  onTransition: Props["onTransition"];
  onRemove: Props["onRemove"];
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: entry.id });
  const [open, setOpen] = useState(false);

  const segStart = entry.start_ms ?? 0;
  const segEnd = entry.end_ms ?? segStart + 1000;
  const trimStart = entry.trim_start_ms ?? segStart;
  const trimEnd = entry.trim_end_ms ?? segEnd;

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="glass w-44 flex-shrink-0 rounded-xl p-2 transition-shadow hover:shadow-[0_12px_32px_-12px_rgba(124,131,245,0.6)]"
    >
      <div
        {...attributes}
        {...listeners}
        className="mb-1.5 flex cursor-grab items-center justify-between text-[11px] text-slate-400 active:cursor-grabbing"
      >
        <span className="font-semibold text-slate-300">Clip {index + 1}</span>
        <span>⠿ drag</span>
      </div>

      <div className="relative aspect-video w-full overflow-hidden rounded bg-black">
        {entry.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={entry.thumbnail_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-[10px] text-slate-600">no preview</div>
        )}
      </div>

      <p className="mt-1.5 line-clamp-2 text-[10px] text-slate-500">{entry.edit_reasoning}</p>

      <div className="mt-1.5 flex items-center justify-between">
        <span className="text-[10px] text-slate-500">
          {((trimEnd - trimStart) / 1000).toFixed(1)}s
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-[10px] font-medium text-accent hover:underline"
        >
          {open ? "close" : "trim/transition"}
        </button>
      </div>

      {open && (
        <div className="mt-2 space-y-2 border-t border-border pt-2">
          <label className="block text-[10px] text-slate-500">
            Start: {((trimStart - segStart) / 1000).toFixed(1)}s
            <input
              type="range"
              min={0}
              max={Math.max(0, segEnd - segStart)}
              step={100}
              value={trimStart - segStart}
              disabled={disabled}
              onChange={(e) => onTrim(entry.id, { trim_start_ms: segStart + Number(e.target.value) })}
              className="mt-0.5 w-full"
            />
          </label>
          <label className="block text-[10px] text-slate-500">
            End: {((trimEnd - segStart) / 1000).toFixed(1)}s
            <input
              type="range"
              min={0}
              max={Math.max(0, segEnd - segStart)}
              step={100}
              value={trimEnd - segStart}
              disabled={disabled}
              onChange={(e) => onTrim(entry.id, { trim_end_ms: segStart + Number(e.target.value) })}
              className="mt-0.5 w-full"
            />
          </label>
          <label className="block text-[10px] text-slate-500">
            Transition in
            <select
              value={entry.transition_in}
              disabled={disabled}
              onChange={(e) => onTransition(entry.id, e.target.value)}
              className="mt-0.5 w-full rounded border border-border bg-surface2 px-1 py-1 text-[10px] text-slate-200"
            >
              {TRANSITIONS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <button
            onClick={() => onRemove(entry.id)}
            disabled={disabled}
            className="w-full rounded border border-red-500/30 bg-red-500/10 py-1 text-[10px] font-medium text-red-300 hover:bg-red-500/20"
          >
            Remove clip
          </button>
        </div>
      )}
    </div>
  );
}
