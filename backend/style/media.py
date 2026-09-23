"""Low-level media decoding + perceptual hashing for style learning."""
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from backend.core.real_ops import FFMPEG, probe_metadata

logger = logging.getLogger(__name__)

FRAME_SIZE = 80                 # decoded frames are FRAME_SIZE×FRAME_SIZE RGB (aspect squashed)
AUDIO_RATE = 8000               # mono analysis sample rate
ENV_HOP_MS = 50                 # audio envelope resolution
MAX_FRAMES = 3600               # cap memory for very long clips


@dataclass
class FrameSet:
    """Frames sampled at a fixed rate. frames: [N, S, S, 3] uint8; times_ms: [N]."""
    frames: np.ndarray
    times_ms: np.ndarray
    fps: float
    width: int
    height: int
    duration_ms: int

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 16 / 9


def sample_frames(path: Path, fps: float, meta: Optional[dict] = None) -> FrameSet:
    meta = meta or probe_metadata(path)
    dur = meta.get("duration_ms") or 0
    if dur:
        fps = min(fps, MAX_FRAMES / max(dur / 1000.0, 1e-3))
    fps = max(fps, 0.2)
    cmd = [FFMPEG, "-v", "error", "-i", str(path),
           "-vf", f"fps={fps:.4f},scale={FRAME_SIZE}:{FRAME_SIZE}:flags=area,format=rgb24",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    fsz = FRAME_SIZE * FRAME_SIZE * 3
    n = len(r.stdout) // fsz
    frames = np.frombuffer(r.stdout[: n * fsz], dtype=np.uint8).reshape(n, FRAME_SIZE, FRAME_SIZE, 3)
    times = (np.arange(n) / fps * 1000.0).astype(np.int64)
    return FrameSet(frames, times, fps, meta.get("width") or 1920, meta.get("height") or 1080,
                    dur or int(n / fps * 1000))


def decode_audio_envelope(path: Path) -> Optional[np.ndarray]:
    """RMS envelope in dBFS, one value per ENV_HOP_MS. None when there is no audio."""
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    pcm = np.frombuffer(r.stdout, dtype=np.float32)
    if pcm.size < AUDIO_RATE // 10:
        return None
    hop = AUDIO_RATE * ENV_HOP_MS // 1000
    n = pcm.size // hop
    rms = np.sqrt(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms + 1e-10)


def envelope_slice(env: Optional[np.ndarray], start_ms: float, end_ms: float) -> np.ndarray:
    if env is None:
        return np.zeros(0, dtype=np.float32)
    a = int(max(0, start_ms) // ENV_HOP_MS)
    b = int(max(0, end_ms) // ENV_HOP_MS)
    return env[a:b]


def detect_black_intervals(path: Path) -> List[Tuple[int, int]]:
    try:
        r = subprocess.run([FFMPEG, "-i", str(path), "-vf", "blackdetect=d=0.1:pix_th=0.10",
                            "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=600)
        return [(int(float(a) * 1000), int(float(b) * 1000)) for a, b in
                re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]
    except Exception as e:  # noqa: BLE001
        logger.debug("blackdetect failed: %s", e)
        return []


# ── Geometry ──────────────────────────────────────────────────────────────────

def content_box(frames: np.ndarray, thresh: float = 18.0) -> Tuple[int, int, int, int]:
    """
    Rows/cols that are usually non-black across the video — strips letterbox /
    pillarbox bars (e.g. a 16:9 shot padded into a 9:16 reel). Uses the median
    over frames so captions/titles drawn inside the bars for part of the video
    don't widen the box. Returns y0,y1,x0,x1.
    """
    if frames.size == 0:
        return 0, FRAME_SIZE, 0, FRAME_SIZE
    luma = frames.mean(axis=3)
    rows = np.where(np.median(luma.max(axis=2), axis=0) > thresh)[0]
    cols = np.where(np.median(luma.max(axis=1), axis=0) > thresh)[0]
    if rows.size < 8 or cols.size < 8:
        return 0, FRAME_SIZE, 0, FRAME_SIZE
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def box_aspect(box: Tuple[int, int, int, int], width: int, height: int) -> float:
    y0, y1, x0, x1 = box
    w_px = (x1 - x0) / FRAME_SIZE * width
    h_px = (y1 - y0) / FRAME_SIZE * height
    return w_px / h_px if h_px else width / max(height, 1)


def crop_box_for_aspect(src_aspect: float, target_aspect: float) -> Tuple[int, int, int, int]:
    """Center crop (in squashed-frame coords) that turns src_aspect into target_aspect."""
    if target_aspect < src_aspect * 0.92:          # e.g. 16:9 raw → 9:16 edit
        frac = target_aspect / src_aspect
        w = max(8, int(round(FRAME_SIZE * frac)))
        x0 = (FRAME_SIZE - w) // 2
        return 0, FRAME_SIZE, x0, x0 + w
    if target_aspect > src_aspect * 1.08:          # vertical raw → horizontal edit
        frac = src_aspect / target_aspect
        h = max(8, int(round(FRAME_SIZE * frac)))
        y0 = (FRAME_SIZE - h) // 2
        return y0, y0 + h, 0, FRAME_SIZE
    return 0, FRAME_SIZE, 0, FRAME_SIZE


# ── Perceptual hashing ────────────────────────────────────────────────────────

def gray(frames: np.ndarray) -> np.ndarray:
    return (frames[..., 0] * 0.299 + frames[..., 1] * 0.587 + frames[..., 2] * 0.114).astype(np.float32)


def phash(frames: np.ndarray, box: Tuple[int, int, int, int]) -> Tuple[np.ndarray, np.ndarray]:
    """
    64-bit DCT perceptual hash per frame (robust to scaling, grading, compression).
    Returns (hashes uint64 [N], informative bool [N]) — flat frames (black,
    white, blown-out) carry no identity and are flagged uninformative.
    """
    import cv2
    y0, y1, x0, x1 = box
    g = gray(frames[:, y0:y1, x0:x1])
    hashes = np.zeros(len(g), dtype=np.uint64)
    informative = np.zeros(len(g), dtype=bool)
    weights = (np.uint64(1) << np.arange(64, dtype=np.uint64))
    for i, img in enumerate(g):
        small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
        informative[i] = small.std() > 6.0
        d = cv2.dct(small)[:8, :8].flatten()
        bits = d > np.median(d[1:])
        hashes[i] = np.sum(weights[bits], dtype=np.uint64)
    return hashes, informative


def hamming_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise Hamming distances between two uint64 hash arrays → [len(a), len(b)] uint8."""
    return np.bitwise_count(a[:, None] ^ b[None, :]).astype(np.uint8)


# ── Color statistics ──────────────────────────────────────────────────────────

def color_stats(frames: np.ndarray, box: Tuple[int, int, int, int]) -> np.ndarray:
    """Per-frame [luma_mean, luma_std, saturation_mean, warmth] on 0..1 scales."""
    y0, y1, x0, x1 = box
    f = frames[:, y0:y1, x0:x1].astype(np.float32) / 255.0
    if f.size == 0:
        return np.zeros((len(frames), 4), dtype=np.float32)
    luma = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
    mx, mn = f.max(axis=3), f.min(axis=3)
    sat = np.where(mx > 1e-3, (mx - mn) / (mx + 1e-6), 0.0)
    warmth = f[..., 0] - f[..., 2]
    n = len(f)
    return np.stack([
        luma.reshape(n, -1).mean(1), luma.reshape(n, -1).std(1),
        sat.reshape(n, -1).mean(1), warmth.reshape(n, -1).mean(1),
    ], axis=1)
