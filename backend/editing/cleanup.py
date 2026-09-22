"""
Silence + filler-word removal ("jump cuts") — pure planner.

For every timeline clip, the ranges to drop are:
  • silences ≥ min_silence_ms (shrunk by `padding_ms` each side so speech isn't clipped)
  • filler words ("um", "uh", …) from the transcript (plus a small pad)
The clip is replaced by the surviving pieces, in order, joined with hard cuts.

Safety: silence removal only applies to clips that contain speech (≥ MIN_WORDS
transcribed words) — otherwise B-roll or music-only clips, which are "silent"
from a speech point of view, would be deleted outright.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from backend.audio.transcribe import FILLERS, normalize

MIN_WORDS = 3
FILLER_PAD_MS = 40


@dataclass
class CleanupOptions:
    remove_silence: bool = True
    min_silence_ms: int = 700
    padding_ms: int = 150
    remove_fillers: bool = True
    extra_fillers: Sequence[str] = ()
    min_piece_ms: int = 250


@dataclass
class CleanupPlan:
    rows: List[dict] = field(default_factory=list)
    removed_ms: int = 0
    silences_removed: int = 0
    fillers_removed: int = 0
    cuts_added: int = 0
    clips_skipped_no_speech: int = 0
    removed_words: List[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {"removed_ms": self.removed_ms, "silences_removed": self.silences_removed,
                "fillers_removed": self.fillers_removed, "cuts_added": self.cuts_added,
                "clips_skipped_no_speech": self.clips_skipped_no_speech,
                "removed_words": self.removed_words[:30], "clip_count": len(self.rows)}


def merge(intervals: List[Tuple[int, int]], join_gap: int = 60) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1] + join_gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def keep_ranges(src_in: int, src_out: int, drop: List[Tuple[int, int]], min_piece: int) -> List[Tuple[int, int]]:
    pieces, cur = [], src_in
    for s, e in merge([(max(s, src_in), min(e, src_out)) for s, e in drop]):
        if s > cur:
            pieces.append((cur, s))
        cur = max(cur, e)
    if cur < src_out:
        pieces.append((cur, src_out))
    return [(s, e) for s, e in pieces if e - s >= min_piece]


def plan_cleanup(rows: List[dict], silences_by_clip: Dict[str, List[Tuple[int, int]]],
                 words_by_clip: Dict[str, List[dict]], opts: CleanupOptions) -> CleanupPlan:
    """rows: timeline rows (layout.rows_from_entries + the entry's persisted fields)."""
    plan = CleanupPlan()
    fillers: Set[str] = set(FILLERS) | {normalize(w) for w in opts.extra_fillers if normalize(w)}
    for r in rows:
        clip = r.get("clip_id") or ""
        s_in, s_out = int(r["src_in"]), int(r["src_out"])
        words = [w for w in words_by_clip.get(clip, []) if w["end_ms"] > s_in and w["start_ms"] < s_out]
        has_speech = sum(1 for w in words if not w.get("is_filler")) >= MIN_WORDS
        drop: List[Tuple[int, int]] = []
        if opts.remove_silence:
            if has_speech:
                for s, e in silences_by_clip.get(clip, []):
                    if e - s >= opts.min_silence_ms:
                        a, b = s + opts.padding_ms, e - opts.padding_ms
                        if b > a and b > s_in and a < s_out:
                            drop.append((a, b))
                            plan.silences_removed += 1
            elif words_by_clip.get(clip) is not None or silences_by_clip.get(clip):
                plan.clips_skipped_no_speech += 1
        if opts.remove_fillers:
            for w in words:
                if normalize(w["word"]) in fillers:
                    drop.append((w["start_ms"] - FILLER_PAD_MS, w["end_ms"] + FILLER_PAD_MS))
                    plan.fillers_removed += 1
                    plan.removed_words.append(w["word"].strip(".,!?;:…\"'"))
        pieces = keep_ranges(s_in, s_out, drop, opts.min_piece_ms) if drop else [(s_in, s_out)]
        kept = sum(e - s for s, e in pieces)
        speed = float((r.get("effects") or {}).get("speed") or 1.0)
        plan.removed_ms += int((s_out - s_in - kept) / speed)
        plan.cuts_added += max(0, len(pieces) - 1)
        for k, (a, b) in enumerate(pieces):
            plan.rows.append({
                "segment_id": r["segment_id"], "narrative_role": r.get("narrative_role"),
                "transition_in": r.get("transition_in") if k == 0 else "cut",
                "trim_start_ms": a, "trim_end_ms": b, "effects": r.get("effects"),
                "reframe_params": r.get("reframe_params"), "edit_reasoning": r.get("edit_reasoning"),
            })
    return plan
