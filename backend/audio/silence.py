"""Silence intervals via ffmpeg `silencedetect` (signal processing, no model)."""
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from backend.core.real_ops import FFMPEG


def detect_silences(path: Path, noise_db: float = -35.0, min_ms: int = 400) -> List[Tuple[int, int]]:
    """[(start_ms, end_ms)] of stretches quieter than noise_db for at least min_ms."""
    r = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_ms / 1000:.3f}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=900,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None:  # silence runs to the end of the file
            dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
            e = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else s
        out.append((max(0, int(s * 1000)), int(e * 1000)))
    return [(s, e) for s, e in out if e - s >= min_ms]
