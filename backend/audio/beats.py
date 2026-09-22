"""
Tempo + beat grid for a music track (numpy only; librosa's numba doesn't run on 3.14).

1. onset strength  : log-magnitude spectral flux (half-wave rectified), local-mean removed
2. tempo           : autocorrelation of the onset envelope × log-normal prior around 120 BPM
3. beats           : Ellis (2007) dynamic programming — beats sit on strong onsets while
                     keeping inter-beat intervals close to the tempo period
4. downbeats       : the 1-in-4 phase with the most onset energy (bar starts)
"""
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from backend.core.real_ops import FFMPEG

SR = 11025
N_FFT = 1024
HOP = 256                      # ≈ 23.2 ms per onset frame
FPS = SR / HOP


# Residual detector latency, measured on synthetic click tracks with known beat
# times (60–150 BPM): after window-centre alignment, beats came out a constant
# ~33 ms early. Re-measure with the click-track test if HOP/N_FFT change.
CALIBRATION_MS = 33


def frame_to_ms(f: float) -> int:
    """Onset frame → time. A Hann-windowed frame 'hears' an onset near its centre,
    and flux frame f is the change from frame f-1 to f, so the event sits between
    the two window centres; plus the measured detector latency."""
    return int(round((f * HOP + N_FFT / 2 - HOP / 2) * 1000.0 / SR + CALIBRATION_MS))


def decode_mono(path: Path, max_sec: Optional[float] = 900) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR)]
    if max_sec:
        cmd += ["-t", str(max_sec)]
    r = subprocess.run(cmd + ["-f", "f32le", "-"], capture_output=True, timeout=900)
    return np.frombuffer(r.stdout, dtype=np.float32)


def onset_envelope(y: np.ndarray) -> np.ndarray:
    if y.size < N_FFT * 2:
        return np.zeros(0, dtype=np.float32)
    n = 1 + (y.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(N_FFT).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    logmag = np.log1p(100.0 * mag)
    flux = np.maximum(0.0, np.diff(logmag, axis=0)).mean(axis=1)
    flux = np.concatenate([[0.0], flux])
    # remove slow loudness trends so quiet and loud sections both yield onsets
    k = int(FPS * 0.5) | 1
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0.0, flux - local)
    sd = env.std()
    return (env / sd).astype(np.float32) if sd > 1e-9 else env.astype(np.float32)


def estimate_tempo(env: np.ndarray, lo_bpm: float = 60, hi_bpm: float = 200, prior_bpm: float = 120) -> float:
    if env.size < FPS * 4:
        return 0.0
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[e.size - 1:]
    lags = np.arange(ac.size)
    valid = (lags >= FPS * 60 / hi_bpm) & (lags <= FPS * 60 / lo_bpm)
    bpm_of_lag = np.where(lags > 0, 60 * FPS / np.maximum(lags, 1), 0)
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpm_of_lag, 1e-6) / prior_bpm) / 1.0) ** 2)
    score = np.where(valid, ac * prior, -np.inf)
    best = int(np.argmax(score))
    # parabolic refinement of the peak lag
    if 1 <= best < ac.size - 1 and np.isfinite(score[best - 1]) and np.isfinite(score[best + 1]):
        a, b, c = score[best - 1], score[best], score[best + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
        best_f = best + float(np.clip(shift, -0.5, 0.5))
    else:
        best_f = float(best)
    return float(60 * FPS / best_f) if best_f > 0 else 0.0


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Ellis DP beat tracker → beat frame indices."""
    if bpm <= 0 or env.size == 0:
        return np.zeros(0, dtype=int)
    period = 60.0 * FPS / bpm
    # smooth onsets with a period-scaled Gaussian so near-beat onsets still count
    w = np.exp(-0.5 * (np.arange(-period, period + 1) * 32.0 / period) ** 2)
    local = np.convolve(env, w, mode="same")
    n = local.size
    score = np.zeros(n)
    back = np.full(n, -1)
    window = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    txcost = -tightness * np.log(-window / period) ** 2
    for t in range(n):
        prev = t + window
        ok = prev >= 0
        if ok.any():
            cand = score[prev[ok]] + txcost[ok]
            j = int(np.argmax(cand))
            score[t] = local[t] + cand[j]
            back[t] = prev[ok][j]
        else:
            score[t] = local[t]
    # end on a strong, well-supported beat near the end
    tail = score[max(0, n - int(2 * period)):]
    t = int(np.argmax(tail)) + max(0, n - int(2 * period))
    beats = [t]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    beats = np.array(beats[::-1])
    # drop beats in leading/trailing silence
    thresh = 0.5 * np.median(local[beats]) if beats.size else 0
    keep = np.where(local[beats] >= thresh)[0]
    return beats[keep[0]:keep[-1] + 1] if keep.size else beats


def analyze(path: Path) -> Dict:
    y = decode_mono(path)
    env = onset_envelope(y)
    bpm = estimate_tempo(env)
    frames = track_beats(env, bpm)
    beats_ms = [frame_to_ms(f) for f in frames]
    downbeat_phase = 0
    if frames.size >= 8:
        strength = [float(env[frames[p::4]].sum()) for p in range(4)]
        downbeat_phase = int(np.argmax(strength))
    return {
        "bpm": round(bpm, 2),
        "beats_ms": beats_ms,
        "downbeat_phase": downbeat_phase,
        "duration_ms": int(y.size * 1000 / SR),
        "version": 1,
    }


def beat_grid(analysis: Dict, start_ms: int, source_offset_ms: int, length_ms: Optional[int],
              loop: bool, total_ms: int, every: int = 1) -> List[int]:
    """Beat times in SEQUENCE ms for a placed music bed (offset / loop / length applied)."""
    beats = analysis.get("beats_ms") or []
    dur = analysis.get("duration_ms") or 0
    if not beats or dur <= 0:
        return []
    end = start_ms + (length_ms if length_ms is not None else max(0, total_ms - start_ms))
    phase = analysis.get("downbeat_phase", 0) if every >= 4 else 0
    picked = [b for i, b in enumerate(beats) if (i - phase) % max(1, every) == 0]
    out, rep = [], 0
    while True:
        base = start_ms - source_offset_ms + rep * dur
        added = False
        for b in picked:
            t = base + b
            if t < start_ms:
                continue
            if t > end:
                break
            out.append(int(t))
            added = True
        rep += 1
        if not loop or base + dur > end or not added and rep > 1:
            break
    return sorted(set(out))
