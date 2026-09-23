"""
Smart reframing for aspect-ratio changes (e.g. 16:9 footage → 9:16 reel).

track_subject():  per-clip subject position over time — the largest face
                  (OpenCV Haar) when there is one, else the centre of visual
                  interest (edge detail + motion). Smoothed like a camera
                  operator: median filter, a dead-zone so small drifts don't
                  pan, and eased motion.
crop_filter():    that path → an ffmpeg `crop` whose x/y are time expressions,
                  applied per part before speed changes (t = source time).
"""
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.core.real_ops import FFMPEG, probe_metadata

logger = logging.getLogger(__name__)

TRACK_VERSION = 1
TRACK_FPS = 5.0
TRACK_W = 320
DEADZONE = 0.06          # fraction of frame width the subject may drift before we pan
EASE = 0.25              # per-sample easing toward the target once outside the dead-zone
MAX_KEYS = 40

_cascade = None


def _faces(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    global _cascade
    import cv2
    if _cascade is None:
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return list(_cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(18, 18)))


def _spectral_saliency(gray: np.ndarray) -> np.ndarray:
    """Spectral-residual saliency (Hou & Zhang 2007): repetitive texture cancels out in
    the log-spectrum, so what's left is what stands out. Returns a 0..1 map, input size."""
    import cv2
    small = cv2.resize(gray, (64, max(8, int(64 * gray.shape[0] / gray.shape[1]))), interpolation=cv2.INTER_AREA)
    f = np.fft.fft2(small)
    log_amp = np.log(np.abs(f) + 1e-8)
    residual = log_amp - cv2.blur(log_amp.astype(np.float32), (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(f)))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 2.5)
    sal = cv2.resize(sal, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    return sal / sal.max() if sal.max() > 0 else sal


def _camera(target: np.ndarray) -> np.ndarray:
    out = np.empty_like(target)
    cam = target[0]
    for i, v in enumerate(target):
        if abs(v - cam) > DEADZONE:
            cam += EASE * (v - cam - np.sign(v - cam) * DEADZONE)
        out[i] = cam
    return out


def smooth_path(raw: np.ndarray) -> np.ndarray:
    """Median-filter, then a dead-zone + eased 'camera operator' run forward AND
    backward and averaged (offline, so zero lag — like filtfilt) so the virtual
    camera pans calmly without trailing a moving subject."""
    if raw.size == 0:
        return raw
    k = 5
    padded = np.pad(raw, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(padded[i:i + k]) for i in range(raw.size)])
    out = 0.5 * (_camera(med) + _camera(med[::-1])[::-1])
    return np.clip(out, 0.0, 1.0)


def track_subject(path: Path) -> Dict:
    """→ {"version", "source": "face"|"interest"|"none", "points": [[t_ms, cx, cy], ...]} (cx/cy in 0..1)."""
    import cv2
    meta = probe_metadata(path)
    w, h = meta.get("width") or 1920, meta.get("height") or 1080
    th = max(2, int(round(TRACK_W * h / w / 2)) * 2)
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf",
                        f"fps={TRACK_FPS},scale={TRACK_W}:{th},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=900)
    n = len(r.stdout) // (TRACK_W * th)
    if n == 0:
        return {"version": TRACK_VERSION, "source": "none", "points": []}
    frames = np.frombuffer(r.stdout[: n * TRACK_W * th], dtype=np.uint8).reshape(n, th, TRACK_W)
    xs, ys = np.full(n, 0.5), np.full(n, 0.5)
    face = np.zeros(n, dtype=bool)
    fxs, fys = np.zeros(n), np.zeros(n)
    prev = None
    for i, f in enumerate(frames):
        faces = _faces(f)
        if faces:
            fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
            fxs[i], fys[i] = (fx + fw / 2) / TRACK_W, (fy + fh / 2) / th
            face[i] = True
        # interest point (fallback when the clip has no usable faces): saliency + motion
        g = f.astype(np.float32)
        s = _spectral_saliency(g)
        if prev is not None:
            m = cv2.GaussianBlur(np.abs(g - prev), (0, 0), 3)
            if m.max() > 8:                       # real motion, not compression noise
                s = s + 2.0 * m / m.max()
        # centroid of the most salient 3% of pixels (peaky; ignores diffuse texture)
        thr = np.quantile(s, 0.97)
        wgt = np.where(s >= thr, s - thr + 1e-6, 0)
        if wgt.sum() > 0:
            yy, xx = np.indices(s.shape)
            xs[i] = float((wgt * xx).sum() / wgt.sum()) / TRACK_W
            ys[i] = float((wgt * yy).sum() / wgt.sum()) / th
        prev = g
    source = "face" if face.sum() >= max(2, 0.3 * n) else "interest"
    if source == "face":
        # frames where the face briefly isn't detected: interpolate between detections
        # (not the interest point, which would make the camera jump)
        idx = np.arange(n)
        xs = np.interp(idx, idx[face], fxs[face])
        ys = np.interp(idx, idx[face], fys[face])
    sx, sy = smooth_path(xs), smooth_path(ys)
    times = (np.arange(n) * 1000.0 / TRACK_FPS).astype(int)
    return {"version": TRACK_VERSION, "source": source,
            "points": [[int(t), round(float(x), 4), round(float(y), 4)] for t, x, y in zip(times, sx, sy)]}


def _downsample(points: List[List[float]], max_keys: int) -> List[List[float]]:
    if len(points) <= max_keys:
        return points
    idx = np.linspace(0, len(points) - 1, max_keys).round().astype(int)
    return [points[i] for i in sorted(set(idx))]


def _piecewise(keys: List[Tuple[float, float]]) -> str:
    """Piecewise-linear expression of t (seconds) through (t, value) keys.
    Commas are NOT escaped: the caller wraps the expression in single quotes."""
    if len(keys) == 1:
        return f"{keys[0][1]:.1f}"
    expr = f"{keys[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys[:-1], keys[1:]))):
        seg = f"{v0:.1f}+({v1 - v0:.1f})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]:.1f},{expr})"


def crop_filter(src_w: int, src_h: int, target_w: int, target_h: int, mode: str,
                track: Optional[Dict] = None, start_ms: int = 0, end_ms: int = 0,
                manual_x: Optional[float] = None) -> Optional[str]:
    """
    ffmpeg crop that makes the source match the target aspect, or None when no
    crop is needed/wanted (same aspect, or mode "fit" → letterbox as before).
    t inside the expression is seconds since the part's in-point.
    """
    if mode == "fit" or not src_w or not src_h:
        return None
    src_a, tgt_a = src_w / src_h, target_w / target_h
    if abs(src_a - tgt_a) / tgt_a < 0.03:
        return None
    horizontal = tgt_a < src_a                      # cropping width (e.g. 16:9 → 9:16)
    cw = int(src_h * tgt_a) // 2 * 2 if horizontal else src_w
    ch = src_h if horizontal else int(src_w / tgt_a) // 2 * 2
    span = (src_w - cw) if horizontal else (src_h - ch)

    def pos(v: float) -> float:
        return float(np.clip(v * (src_w if horizontal else src_h) - (cw if horizontal else ch) / 2, 0, span))

    if manual_x is not None:
        expr = f"{pos(manual_x):.1f}"
    elif mode == "smart" and track and track.get("points"):
        axis = 1 if horizontal else 2
        pts = [p for p in track["points"] if start_ms - 400 <= p[0] <= end_ms + 400] or track["points"]
        keys = [((p[0] - start_ms) / 1000.0, pos(p[axis])) for p in _downsample(pts, MAX_KEYS)]
        keys = [(max(0.0, t), v) for t, v in keys]
        # collapse near-static paths to a constant (cheaper, and no micro-jitter)
        vals = [v for _, v in keys]
        expr = f"{np.median(vals):.1f}" if max(vals) - min(vals) < 4 else _piecewise(keys)
    else:  # center
        expr = f"{span / 2:.1f}"
    x, y = (f"'{expr}'", f"{(src_h - ch) // 2}") if horizontal else (f"{(src_w - cw) // 2}", f"'{expr}'")
    return f"crop={cw}:{ch}:{x}:{y}"
