"use client";

interface Props {
  src: string | null;
  label?: string;
}

export default function VideoPlayer({ src, label }: Props) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-black shadow-[0_20px_60px_-20px_rgba(124,131,245,0.45)]">
      {src ? (
        <video src={src} controls className="aspect-video w-full bg-black" />
      ) : (
        <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 bg-black/50 text-center text-xs text-slate-500">
          <span>No render yet</span>
          <span>Edit the timeline below, then hit Re-render to preview.</span>
        </div>
      )}
      {label && <div className="border-t border-border bg-surface px-3 py-1.5 text-[11px] text-slate-400">{label}</div>}
    </div>
  );
}
