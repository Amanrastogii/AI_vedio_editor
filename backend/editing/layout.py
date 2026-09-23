"""
Sequence timing of timeline entries — the same arithmetic as
render_graph.part_output_duration + build_join_filter (and frontend/lib/timeline.ts),
so anything placed in sequence time (captions, beats) lines up with the render.
"""
from dataclasses import dataclass
from typing import List, Optional

from backend.core.render_graph import TRANSITION_SEC, XFADE_MAP, normalize_effects


@dataclass
class Placed:
    index: int
    entry_id: Optional[str]
    clip_id: Optional[str]
    src_in: int          # source-clip ms
    src_out: int
    speed: float
    start: float         # sequence ms
    end: float
    duration: float
    overlap_in: float    # crossfade overlap with the previous clip (ms)
    transition_in: str

    def seq_to_src(self, t: float) -> float:
        return self.src_in + (t - self.start) * self.speed

    def src_to_seq(self, s: float) -> float:
        return self.start + (s - self.src_in) / self.speed


def place(rows: List[dict]) -> List[Placed]:
    """rows: [{"entry_id","clip_id","src_in","src_out","effects","transition_in"}] in timeline order."""
    out: List[Placed] = []
    total = 0.0
    for i, r in enumerate(rows):
        speed = normalize_effects(r.get("effects"))["speed"]
        dur = max(300.0, float(r["src_out"] - r["src_in"])) / speed
        tr = r.get("transition_in") or "cut"
        overlap = 0.0
        if i > 0 and XFADE_MAP.get(tr):
            prev = out[-1].duration
            d = min(TRANSITION_SEC * 1000, prev / 2, dur / 2, total / 2)
            overlap = max(50.0, d)
        start = total - overlap
        out.append(Placed(i, r.get("entry_id"), r.get("clip_id"), int(r["src_in"]), int(r["src_out"]), speed,
                          start, start + dur, dur, overlap, tr))
        total = start + dur
    return out


def total_ms(placed: List[Placed]) -> float:
    return placed[-1].end if placed else 0.0


def rows_from_entries(entries) -> List[dict]:
    """StoryTimeline ORM rows (with .segment loaded) → layout rows."""
    rows = []
    for e in entries:
        if not e.segment:
            continue
        s = e.trim_start_ms if e.trim_start_ms is not None else e.segment.start_ms
        t = e.trim_end_ms if e.trim_end_ms is not None else e.segment.end_ms
        rows.append({"entry_id": str(e.id), "clip_id": str(e.segment.clip_id), "src_in": s, "src_out": t,
                     "effects": e.effects, "transition_in": e.transition_in.value,
                     "seg_start": e.segment.start_ms, "seg_end": e.segment.end_ms})
    return rows
