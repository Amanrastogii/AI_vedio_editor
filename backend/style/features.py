"""
Per-window shot features. The SAME code featurizes the editor's raw footage at
training time and new footage at apply time — no train/serve skew.

A "window" is a shot, or a ≤6s slice of a long shot: editors often keep only
part of a long take, so decisions are learned at sub-shot granularity.
"""
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from backend.style import media

logger = logging.getLogger(__name__)

FEATURE_VERSION = 2   # bump whenever features change → cached example analyses are recomputed
FEATURE_NAMES = [
    "log_duration", "rel_position", "sharpness", "brightness", "contrast", "saturation",
    "motion", "motion_var", "audio_db", "audio_peak_db", "audio_activity",
    "faces", "face_area", "sharpness_pct", "motion_pct", "audio_pct", "brightness_pct",
]
FEATURE_LABELS = {
    "log_duration": "longer shots", "rel_position": "later in the clip", "sharpness": "sharp footage",
    "brightness": "bright footage", "contrast": "high contrast", "saturation": "colorful footage",
    "motion": "lots of motion", "motion_var": "changing motion", "audio_db": "loud audio",
    "audio_peak_db": "audio peaks", "audio_activity": "continuous sound / speech",
    "faces": "people on screen", "face_area": "close-ups of faces",
    "sharpness_pct": "the sharpest part of a clip", "motion_pct": "the most dynamic part of a clip",
    "audio_pct": "the loudest part of a clip", "brightness_pct": "the brightest part of a clip",
}

WINDOW_MAX_MS = 6000
WINDOW_TARGET_MS = 4000
ANALYSIS_FPS = 4.0


def windows_for_shot(start_ms: int, end_ms: int) -> List[Tuple[int, int]]:
    dur = end_ms - start_ms
    if dur <= WINDOW_MAX_MS:
        return [(start_ms, end_ms)]
    n = math.ceil(dur / WINDOW_TARGET_MS)
    step = dur / n
    return [(int(start_ms + i * step), int(start_ms + (i + 1) * step)) for i in range(n)]


@dataclass
class ClipAnalysis:
    """Decoded, reusable media for one clip (frames + audio envelope)."""
    path: Path
    frames: media.FrameSet
    audio_env: Optional[np.ndarray]
    sharp: np.ndarray        # per-frame sharpness
    diff: np.ndarray         # per-frame motion (diff to previous)
    colors: np.ndarray       # per-frame color stats


def analyze_clip(path: Path, fps: float = ANALYSIS_FPS) -> ClipAnalysis:
    import cv2
    fs = media.sample_frames(path, fps)
    g = media.gray(fs.frames) if len(fs.frames) else np.zeros((0, media.FRAME_SIZE, media.FRAME_SIZE), np.float32)
    sharp = np.array([cv2.Laplacian(x, cv2.CV_32F).var() for x in g], dtype=np.float32) if len(g) else np.zeros(0)
    diff = np.zeros(len(g), dtype=np.float32)
    if len(g) > 1:
        diff[1:] = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2)) / 255.0
    colors = media.color_stats(fs.frames, (0, media.FRAME_SIZE, 0, media.FRAME_SIZE))
    return ClipAnalysis(path, fs, media.decode_audio_envelope(path), sharp, diff, colors)


_face_cascade = None


def _faces_at(path: Path, t_ms: int) -> Tuple[int, float]:
    """(face count, largest face area fraction) on a full-res frame — OpenCV Haar, CPU."""
    global _face_cascade
    try:
        import cv2
        if _face_cascade is None:
            _face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return 0, 0.0
        h, w = frame.shape[:2]
        scale = 480.0 / max(w, 1)
        small = cv2.resize(frame, (480, max(1, int(h * scale))))
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
        if len(faces) == 0:
            return 0, 0.0
        area = max(fw * fh for (_, _, fw, fh) in faces) / float(gray.shape[0] * gray.shape[1])
        return int(len(faces)), float(area)
    except Exception as e:  # noqa: BLE001
        logger.debug("face detect failed: %s", e)
        return 0, 0.0


def _pct_rank(values: np.ndarray) -> np.ndarray:
    """Percentile rank within a clip; ties share their average rank (a silent clip's
    audio windows are all 0.5, not an arbitrary ordering the model could learn noise from)."""
    if len(values) <= 1:
        return np.full(len(values), 0.5)
    s = np.sort(values)
    lo = np.searchsorted(s, values, side="left")
    hi = np.searchsorted(s, values, side="right") - 1
    return (lo + hi) / 2.0 / (len(values) - 1)


def window_features(ca: ClipAnalysis, windows: List[Tuple[int, int]]) -> np.ndarray:
    """[len(windows), len(FEATURE_NAMES)] feature matrix for windows of ONE clip."""
    t = ca.frames.times_ms
    dur = max(ca.frames.duration_ms, 1)
    rows = []
    for (s, e) in windows:
        idx = np.where((t >= s) & (t < e))[0]
        if idx.size == 0 and len(t):
            idx = np.array([int(np.argmin(np.abs(t - (s + e) / 2)))])
        sharp = float(np.log1p(ca.sharp[idx].mean())) if idx.size else 0.0
        col = ca.colors[idx].mean(axis=0) if idx.size else np.zeros(4)
        mot = ca.diff[idx[1:]] if idx.size > 1 else np.zeros(1)
        env = media.envelope_slice(ca.audio_env, s, e)
        a_db = float(env.mean()) if env.size else -90.0
        a_pk = float(np.percentile(env, 95)) if env.size else -90.0
        a_act = float((env > -35).mean()) if env.size else 0.0
        faces, area = _faces_at(ca.path, (s + e) // 2)
        rows.append([
            math.log1p((e - s) / 1000.0), ((s + e) / 2) / dur, sharp,
            float(col[0]), float(col[1]), float(col[2]),
            float(mot.mean()), float(mot.std()), max(a_db, -90.0), max(a_pk, -90.0), a_act,
            float(min(faces, 5)), area,
        ])
    X = np.array(rows, dtype=np.float64).reshape(len(rows), 13)
    pct = np.stack([_pct_rank(X[:, 2]), _pct_rank(X[:, 6]), _pct_rank(X[:, 8]), _pct_rank(X[:, 3])], axis=1) \
        if len(rows) else np.zeros((0, 4))
    return np.concatenate([X, pct], axis=1)


def describe_window(f: List[float]) -> str:
    """Short human description of a window's features — shown next to retrieved memories."""
    d = dict(zip(FEATURE_NAMES, f))
    bits = [f"{math.expm1(d['log_duration']):.1f}s"]
    if d["faces"] >= 1:
        bits.append("face" + ("s" if d["faces"] > 1 else ""))
    bits.append("high motion" if d["motion_pct"] > 0.66 else "calm" if d["motion_pct"] < 0.33 else "some motion")
    if d["audio_activity"] > 0.6:
        bits.append("active audio")
    if d["sharpness_pct"] > 0.66:
        bits.append("sharpest in clip")
    return ", ".join(bits)
