"""
Snap cut points to the music's beat grid — pure planner.

Walks the timeline in order; for each clip picks the beat nearest its natural
end (never shorter than `min_clip_ms`) whose required length still fits inside
the clip's source segment — extending the out-point, or pulling the in-point
earlier when the out-point can't move. For crossfades, the transition midpoint
is what lands on the beat. Clips that can't reach any beat keep their length
(reported as unsynced) and the walk continues from their real end.
"""
import bisect
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from backend.core.render_graph import TRANSITION_SEC, XFADE_MAP, normalize_effects
from backend.editing.layout import place


@dataclass
class BeatSyncPlan:
    rows: List[dict] = field(default_factory=list)
    synced: int = 0
    unsynced: int = 0
    mean_error_ms: Optional[float] = None
    every: int = 1

    def stats(self) -> dict:
        return {"synced": self.synced, "unsynced": self.unsynced, "every": self.every,
                "mean_error_ms": None if self.mean_error_ms is None else round(self.mean_error_ms, 1)}


def auto_every(beats: List[int], rows: List[dict]) -> int:
    """Beat stride whose spacing best matches the current median clip length."""
    if len(beats) < 3 or not rows:
        return 1
    ibi = float(np.median(np.diff(beats)))
    lens = [(r["src_out"] - r["src_in"]) / normalize_effects(r.get("effects"))["speed"] for r in rows]
    med = float(np.median(lens))
    return min((1, 2, 4, 8), key=lambda k: abs(np.log((k * ibi) / max(med, 1))))


def boundary_errors(rows: List[dict], beats: List[int]) -> List[float]:
    placed = place(rows)
    errs = []
    for i in range(1, len(placed)):
        b = placed[i].start + placed[i].overlap_in / 2
        if beats and beats[0] - 50 <= b <= beats[-1] + 50:
            j = bisect.bisect_left(beats, b)
            errs.append(min(abs(b - beats[k]) for k in (j - 1, j) if 0 <= k < len(beats)))
    return errs


def plan_beat_sync(rows: List[dict], beats: List[int], min_clip_ms: int = 400,
                   every: int = 1) -> BeatSyncPlan:
    """rows need src_in/src_out/seg_start/seg_end/effects/transition_in (+ passthrough fields).
    `beats` must already be the chosen grid in sequence ms (see audio.beats.beat_grid)."""
    plan = BeatSyncPlan(every=every)
    beats = sorted(beats)
    out = [dict(r) for r in rows]
    cursor = 0.0            # sequence time where the current clip starts
    for i, r in enumerate(out):
        speed = normalize_effects(r.get("effects"))["speed"]
        dur = (r["src_out"] - r["src_in"]) / speed
        nxt = out[i + 1] if i + 1 < len(out) else None
        half = 0.0
        if nxt is not None and XFADE_MAP.get(nxt.get("transition_in") or "cut"):
            half = min(TRANSITION_SEC * 1000, dur / 2) / 2
        natural = cursor + dur - half                        # where this clip's cut point is now
        if nxt is None or not beats or natural > beats[-1] + 200:
            plan.unsynced += 0 if nxt is None else 1
            cursor = cursor + dur - 2 * half
            continue
        seg_room = (r["seg_end"] - r["seg_start"]) / speed    # the most sequence time this clip could fill
        cands = [b for b in beats if b - cursor >= min_clip_ms and b - cursor + half <= seg_room + 1]
        if not cands:
            plan.unsynced += 1
            cursor = cursor + dur - 2 * half
            continue
        target = min(cands, key=lambda b: (abs(b - natural), b))
        need_src = int(round((target - cursor + half) * speed))
        s_in = r["src_in"]
        if s_in + need_src > r["seg_end"]:
            s_in = max(r["seg_start"], r["seg_end"] - need_src)
        r["src_in"], r["src_out"] = s_in, s_in + need_src
        plan.synced += 1
        cursor = target - half
    errs = boundary_errors(out, beats)
    plan.mean_error_ms = float(np.mean(errs)) if errs else None
    plan.rows = [{
        "segment_id": r["segment_id"], "narrative_role": r.get("narrative_role"),
        "transition_in": r.get("transition_in"), "trim_start_ms": r["src_in"], "trim_end_ms": r["src_out"],
        "effects": r.get("effects"), "reframe_params": r.get("reframe_params"),
        "edit_reasoning": r.get("edit_reasoning"),
    } for r in out]
    return plan
