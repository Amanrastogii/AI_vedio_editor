"""
Recover editing decisions by aligning a finished edit back onto its raw footage.

Every sampled frame of the edit is matched to its nearest raw frame by 64-bit
DCT perceptual hash (robust to re-encoding, scaling, color grading and
letterboxing; crops are handled by hashing the raw with the edit's aspect).
Per edited shot, a robust (Theil–Sen) line through the matched
(edit time → raw time) pairs yields the source in/out points and the playback
speed. Shot boundaries are refined wherever the matched source jumps, so a
cut the scene detector missed is still recovered.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend.style import media
from backend.style.features import ClipAnalysis

MATCH_THRESH = 12          # max Hamming distance (of 64 bits) for a frame match
JUMP_MS = 1200             # source-offset jump that splits a shot
MIN_SHOT_MS = 300


@dataclass
class FrameMatch:
    t_edit: int
    clip: int = -1
    t_raw: int = -1
    raw_idx: int = -1
    dist: int = 64
    informative: bool = True


@dataclass
class ShotMap:
    start_ms: int
    end_ms: int
    matched: bool = False
    clip: int = -1
    src_in: int = 0
    src_out: int = 0
    speed: float = 1.0
    match_ratio: float = 0.0
    transition_in: str = "cut"
    frame_ids: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in
             ("start_ms", "end_ms", "matched", "clip", "src_in", "src_out", "speed", "match_ratio",
              "transition_in")}
        d["speed"] = round(float(d["speed"]), 3)
        d["match_ratio"] = round(float(d["match_ratio"]), 3)
        return d


def match_frames(edited: media.FrameSet, raws: Sequence[ClipAnalysis]
                 ) -> Tuple[List[FrameMatch], Tuple[int, int, int, int], List[Tuple[int, int, int, int]]]:
    e_box = media.content_box(edited.frames)
    e_aspect = media.box_aspect(e_box, edited.width, edited.height)
    e_hash, e_inf = media.phash(edited.frames, e_box)

    all_hash, all_clip, all_t, all_idx, r_boxes = [], [], [], [], []
    for ci, ca in enumerate(raws):
        box = media.crop_box_for_aspect(ca.frames.aspect, e_aspect)
        r_boxes.append(box)
        h, inf = media.phash(ca.frames.frames, box)
        keep = np.where(inf)[0]
        all_hash.append(h[keep])
        all_clip.append(np.full(keep.size, ci))
        all_t.append(ca.frames.times_ms[keep])
        all_idx.append(keep)
    matches = [FrameMatch(int(t), informative=bool(inf)) for t, inf in zip(edited.times_ms, e_inf)]
    if not all_hash or sum(a.size for a in all_hash) == 0:
        return matches, e_box, r_boxes
    R = np.concatenate(all_hash)
    C, T, I = np.concatenate(all_clip), np.concatenate(all_t), np.concatenate(all_idx)

    for s in range(0, len(e_hash), 256):
        D = media.hamming_matrix(e_hash[s:s + 256], R)
        best = D.argmin(axis=1)
        for j, b in enumerate(best):
            i = s + j
            d = int(D[j, b])
            if e_inf[i] and d <= MATCH_THRESH:
                m = matches[i]
                m.clip, m.t_raw, m.raw_idx, m.dist = int(C[b]), int(T[b]), int(I[b]), d
    return matches, e_box, r_boxes


SPEED_MIN_POINTS = 5
SPEED_MIN_SHOT_MS = 1000
SPEED_MAX_RESID_MS = 180


def _theil_sen(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    if len(x) < 2:
        return 1.0, float(np.median(y - x)) if len(x) else 0.0
    slopes = []
    for i in range(len(x)):
        for j in range(i + 1, min(len(x), i + 25)):
            if x[j] != x[i]:
                slopes.append((y[j] - y[i]) / (x[j] - x[i]))
    slope = float(np.median(slopes)) if slopes else 1.0
    if not (0.2 <= slope <= 5.0):
        slope = 1.0
    return slope, float(np.median(y - slope * x))


def _split_on_jumps(shot: Tuple[int, int], ms: List[FrameMatch]) -> List[Tuple[int, int]]:
    """Split a detected shot wherever the matched source clip/offset jumps (confirmed by 2 frames)."""
    good = [m for m in ms if m.clip >= 0]
    if len(good) < 4:
        return [shot]
    cuts = []
    for k in range(1, len(good) - 1):
        a, b, c = good[k - 1], good[k], good[k + 1]
        off_a, off_b, off_c = a.t_raw - a.t_edit, b.t_raw - b.t_edit, c.t_raw - c.t_edit
        jump_b = b.clip != a.clip or abs(off_b - off_a) > JUMP_MS
        confirm = c.clip == b.clip and abs(off_c - off_b) <= JUMP_MS
        if jump_b and confirm:
            cuts.append((a.t_edit + b.t_edit) // 2)
    bounds = [shot[0], *cuts, shot[1]]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] - bounds[i] >= MIN_SHOT_MS]


def map_shots(shots: List[Tuple[int, int]], matches: List[FrameMatch],
              raw_durations: List[int]) -> List[ShotMap]:
    t = np.array([m.t_edit for m in matches])
    refined: List[Tuple[int, int]] = []
    for (s, e) in shots:
        idx = np.where((t >= s) & (t < e))[0]
        refined.extend(_split_on_jumps((s, e), [matches[i] for i in idx]))

    out: List[ShotMap] = []
    for (s, e) in refined:
        idx = [int(i) for i in np.where((t >= s) & (t < e))[0]]
        sm = ShotMap(s, e, frame_ids=idx)
        good = [matches[i] for i in idx if matches[i].clip >= 0]
        if good:
            clips = np.array([m.clip for m in good])
            clip = int(np.bincount(clips).argmax())
            pts = [m for m in good if m.clip == clip]
            x = np.array([m.t_edit for m in pts], dtype=np.float64)
            y = np.array([m.t_raw for m in pts], dtype=np.float64)
            slope, icpt = _theil_sen(x, y)
            resid = np.abs(y - (slope * x + icpt))
            inl = resid <= 1500
            if inl.sum() >= 2 and inl.sum() < len(x):
                slope, icpt = _theil_sen(x[inl], y[inl])
            # A speed change is only believed when it explains the matches much
            # better than normal speed does (model selection): near-static
            # footage matches many raw times and fits any slope about equally.
            xi, yi = x[inl], y[inl]
            fit_resid = float(np.median(np.abs(yi - (slope * xi + icpt)))) if inl.any() else 1e9
            unit_resid = float(np.median(np.abs(yi - xi - np.median(yi - xi)))) if inl.any() else 0.0
            trusted = (inl.sum() >= SPEED_MIN_POINTS and (e - s) >= SPEED_MIN_SHOT_MS
                       and fit_resid <= SPEED_MAX_RESID_MS
                       and unit_resid >= max(250.0, 2.5 * fit_resid))
            if abs(slope - 1.0) < 0.12 or not trusted:
                slope = 1.0
                icpt = float(np.median(y[inl] - x[inl])) if inl.any() else icpt
            dur = raw_durations[clip] if clip < len(raw_durations) else 10 ** 9
            sm.matched = True
            sm.clip = clip
            sm.speed = slope
            sm.src_in = int(np.clip(slope * s + icpt, 0, dur))
            sm.src_out = int(np.clip(slope * e + icpt, 0, dur))
            sm.match_ratio = len(pts) / max(1, len(idx))
            if sm.src_out - sm.src_in < 200:
                sm.matched = False
        out.append(sm)
    return out


def classify_transitions(shots: List[ShotMap], edited: media.FrameSet,
                         black: List[Tuple[int, int]], matches: Optional[List[FrameMatch]] = None) -> None:
    """
    Label each shot's transition_in:
    - fade_to_black: a black interval sits on the boundary
    - dissolve: frames AT the boundary are real picture (informative) yet match
      no raw frame — they're blends of the two shots — while frames on both
      sides match cleanly, and the change across the boundary is gradual
    - cut: otherwise (every frame belongs to one shot or the other)
    Content motion alone never counts as a dissolve, which keeps fast
    footage from being mislabeled.
    """
    g = media.gray(edited.frames) if len(edited.frames) else None
    t = edited.times_ms
    for k in range(1, len(shots)):
        b = shots[k].start_ms
        if any(bs - 300 <= b <= be + 300 for (bs, be) in black):
            shots[k].transition_in = "fade_to_black"
            continue
        if g is None or not matches:
            continue
        near = np.where((t >= b - 400) & (t <= b + 400))[0]
        blends = [i for i in near if matches[i].informative and matches[i].clip < 0]
        if not blends:
            continue
        before = [m for m in matches if b - 1500 <= m.t_edit < b - 400 and m.clip >= 0]
        after = [m for m in matches if b + 400 < m.t_edit <= b + 1500 and m.clip >= 0]
        if not before or not after:
            continue
        idx = np.where((t >= b - 700) & (t <= b + 700))[0]
        if idx.size >= 4:
            diffs = np.abs(np.diff(g[idx], axis=0)).mean(axis=(1, 2))
            total = diffs.sum()
            if total > 12 and diffs.max() / total < 0.6:
                shots[k].transition_in = "dissolve"


def color_look(matches: List[FrameMatch], edited: media.FrameSet, e_box, raws: Sequence[ClipAnalysis],
               r_boxes) -> Optional[Dict[str, float]]:
    """Median grade the editor applied: edited vs raw color stats on matched frame pairs."""
    pairs = [(i, m) for i, m in enumerate(matches) if m.clip >= 0]
    if len(pairs) < 5:
        return None
    e_stats = media.color_stats(edited.frames[[i for i, _ in pairs]], e_box)
    r_stats = np.stack([
        media.color_stats(raws[m.clip].frames.frames[m.raw_idx:m.raw_idx + 1], r_boxes[m.clip])[0]
        for _, m in pairs])
    ok = (r_stats[:, 1] > 0.02) & (r_stats[:, 2] > 0.02)
    if ok.sum() < 5:
        return None
    e_stats, r_stats = e_stats[ok], r_stats[ok]
    return {
        "brightness": float(np.median(e_stats[:, 0] - r_stats[:, 0])),
        "contrast": float(np.median(e_stats[:, 1] / r_stats[:, 1])),
        "saturation": float(np.median(e_stats[:, 2] / r_stats[:, 2])),
        "warmth": float(np.median(e_stats[:, 3] - r_stats[:, 3])),
        "samples": int(ok.sum()),
    }


def audio_relationship(shots: List[ShotMap], edited_env: Optional[np.ndarray],
                       raws: Sequence[ClipAnalysis]) -> Dict[str, object]:
    """
    Did the editor keep the original sound? Correlate the edit's loudness
    envelope with the matched raw ranges: high → original audio kept,
    low → replaced by music / voice-over.
    """
    if edited_env is None:
        return {"mode": "silent", "correlation": None, "loudness_db": None}
    corrs, weights = [], []
    for sm in shots:
        if not sm.matched or raws[sm.clip].audio_env is None:
            continue
        e = media.envelope_slice(edited_env, sm.start_ms, sm.end_ms)
        r = media.envelope_slice(raws[sm.clip].audio_env, sm.src_in, sm.src_out)
        if e.size < 6 or r.size < 6:
            continue
        r = np.interp(np.linspace(0, r.size - 1, e.size), np.arange(r.size), r)
        if e.std() < 0.5 or r.std() < 0.5:
            continue
        corrs.append(float(np.corrcoef(e, r)[0, 1]))
        weights.append(sm.end_ms - sm.start_ms)
    loud = float(np.mean(edited_env[edited_env > -70])) if (edited_env > -70).any() else None
    if not corrs:
        return {"mode": "unknown", "correlation": None, "loudness_db": loud}
    c = float(np.average(corrs, weights=weights))
    mode = "original" if c >= 0.5 else "mixed" if c >= 0.25 else "replaced"
    return {"mode": mode, "correlation": round(c, 3), "loudness_db": loud}
