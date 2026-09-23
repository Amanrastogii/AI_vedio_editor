"""One training example (finished edit + its raw clips) → analysis + labeled samples."""
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from backend.core import real_ops
from backend.style import align, features, media

logger = logging.getLogger(__name__)

EDIT_FPS = 5.0


def _kendall_tau(a: List[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    s = 0
    for i in range(n):
        for j in range(i + 1, n):
            s += np.sign(a[j] - a[i])
    return float(s / (n * (n - 1) / 2))


def _merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 100:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _overlap(win: Tuple[int, int], ranges: List[Tuple[int, int]]) -> Tuple[int, Optional[int]]:
    """(kept ms inside window, first kept ms) for a window against merged kept ranges."""
    s, e = win
    kept, first = 0, None
    for rs, re_ in ranges:
        a, b = max(s, rs), min(e, re_)
        if b > a:
            kept += b - a
            first = a if first is None else min(first, a)
    return kept, first


def analyze_example(edited_path: Path, raw_paths: List[Path], raw_names: List[str],
                    progress: Callable[[str], None] = lambda m: None) -> Dict:
    progress("Decoding the finished edit")
    e_meta = real_ops.probe_metadata(edited_path)
    edited = media.sample_frames(edited_path, EDIT_FPS, e_meta)
    edited_env = media.decode_audio_envelope(edited_path)
    e_dur = edited.duration_ms
    shots = real_ops.detect_scenes(edited_path, e_dur) or [(0, e_dur)]

    raws: List[features.ClipAnalysis] = []
    for i, p in enumerate(raw_paths):
        progress(f"Decoding raw clip {i + 1}/{len(raw_paths)}")
        raws.append(features.analyze_clip(p))

    progress("Aligning the edit to the raw footage")
    matches, e_box, r_boxes = align.match_frames(edited, raws)
    shot_maps = align.map_shots(shots, matches, [r.frames.duration_ms for r in raws])
    align.classify_transitions(shot_maps, edited, media.detect_black_intervals(edited_path), matches)

    progress("Measuring color grade & audio")
    look = align.color_look(matches, edited, e_box, raws, r_boxes)
    audio = align.audio_relationship(shot_maps, edited_env, raws)

    matched = [s for s in shot_maps if s.matched]
    lens = np.array([s.end_ms - s.start_ms for s in shot_maps], dtype=float)
    coverage = sum(s.end_ms - s.start_ms for s in matched) / max(e_dur, 1)
    trans_counts: Dict[str, int] = {}
    for s in shot_maps[1:]:
        trans_counts[s.transition_in] = trans_counts.get(s.transition_in, 0) + 1

    # Story order: does the editor follow the footage chronologically?
    offsets = np.cumsum([0] + [r.frames.duration_ms for r in raws])
    src_keys = [float(offsets[s.clip] + s.src_in) for s in matched]
    tau = _kendall_tau(src_keys)
    hook_from_later = None
    if len(src_keys) >= 3:
        rank = sorted(src_keys).index(src_keys[0]) / (len(src_keys) - 1)
        hook_from_later = bool(rank > 0.3)

    # ── Labeled samples: every raw window, kept or dropped ──
    progress("Extracting what you kept vs cut")
    kept_by_clip: Dict[int, List[Tuple[int, int]]] = {}
    shot_by_clip: Dict[int, List[align.ShotMap]] = {}
    for s in matched:
        kept_by_clip.setdefault(s.clip, []).append((s.src_in, s.src_out))
        shot_by_clip.setdefault(s.clip, []).append(s)
    samples = []
    for ci, (ca, path) in enumerate(zip(raws, raw_paths)):
        dur = ca.frames.duration_ms
        raw_shots = real_ops.detect_scenes(path, dur) or [(0, dur)]
        wins = [w for (s, e) in raw_shots for w in features.windows_for_shot(s, e) if w[1] - w[0] >= 400]
        if not wins:
            continue
        X = features.window_features(ca, wins)
        kept_ranges = _merge_ranges(kept_by_clip.get(ci, []))
        for w, f in zip(wins, X):
            wdur = w[1] - w[0]
            kept_ms, first = _overlap(w, kept_ranges)
            kept = kept_ms >= min(1000, 0.3 * wdur)
            decision = {"clip": ci, "start_ms": w[0], "end_ms": w[1]}
            if kept:
                best = max(shot_by_clip.get(ci, []), default=None,
                           key=lambda s: min(w[1], s.src_out) - max(w[0], s.src_in))
                decision.update({
                    "head_frac": round((first - w[0]) / wdur, 3) if first is not None else 0.0,
                    "keep_frac": round(min(1.0, kept_ms / wdur), 3),
                    "speed": round(best.speed, 3) if best else 1.0,
                    "edit_position": round(best.start_ms / max(e_dur, 1), 3) if best else None,
                    "transition_in": best.transition_in if best else "cut",
                })
            name = raw_names[ci] if ci < len(raw_names) else f"clip {ci + 1}"
            samples.append({
                "features": [round(float(v), 5) for v in f],
                "kept": bool(kept),
                "decision": decision,
                "description": features.describe_window(f.tolist()),
                "source": f"{name} @ {w[0] / 1000:.1f}s",
            })

    raw_total = int(sum(r.frames.duration_ms for r in raws))
    speeds = [s.speed for s in matched]
    return {
        "feature_version": features.FEATURE_VERSION,
        "edited": {"duration_ms": e_dur, "width": edited.width, "height": edited.height,
                   "shot_count": len(shot_maps)},
        "raw_total_ms": raw_total,
        "duration_ratio": round(e_dur / raw_total, 4) if raw_total else None,
        "coverage": round(float(coverage), 3),
        "shots": [s.to_dict() for s in shot_maps],
        "pacing": {
            "median_shot_ms": float(np.median(lens)) if lens.size else None,
            "mean_shot_ms": float(lens.mean()) if lens.size else None,
            "p25_shot_ms": float(np.percentile(lens, 25)) if lens.size else None,
            "p75_shot_ms": float(np.percentile(lens, 75)) if lens.size else None,
            "cuts_per_min": round(max(0, len(shot_maps) - 1) / max(e_dur / 60000.0, 1e-3), 2),
        },
        "transitions": trans_counts,
        "speed": {
            "median": float(np.median(speeds)) if speeds else 1.0,
            "changed_fraction": float(np.mean([abs(s - 1) > 0.05 for s in speeds])) if speeds else 0.0,
        },
        "color": look,
        "audio": audio,
        "order": {"kendall_tau": tau, "hook_from_later": hook_from_later},
        "samples": samples,
        "kept_windows": int(sum(1 for s in samples if s["kept"])),
        "total_windows": len(samples),
    }
