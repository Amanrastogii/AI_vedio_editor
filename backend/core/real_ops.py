"""
Real (CPU-only) media operations for LOCAL_MODE.

These run genuinely on this machine — no GPU, no cloud:
- probe_metadata : real ffprobe
- detect_scenes  : real PySceneDetect content-aware shot detection
- extract_keyframe / quality_score : real OpenCV
- render_edit    : real ffmpeg trim + scale/pad + concat → a true edited MP4

Everything is best-effort with graceful fallbacks so the pipeline never hard-fails
on a weird input.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger(__name__)


def _resolve_binary(name: str, configured: str) -> str:
    """
    Resolve an absolute path to ffmpeg/ffprobe so subprocess calls work
    regardless of the server process's PATH (preview-launched uvicorn may not
    inherit the user's PATH that contains the WinGet/Homebrew shims).
    """
    candidates = [configured, name]
    for cand in candidates:
        found = shutil.which(cand)
        if found:
            return found
    # Common Windows WinGet location
    winget = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if winget.exists():
        return str(winget)
    # Common POSIX locations
    for p in (f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"):
        if Path(p).exists():
            return p
    logger.warning("Could not resolve absolute path for %s; falling back to bare name", name)
    return configured


FFMPEG = _resolve_binary("ffmpeg", settings.FFMPEG_PATH)
FFPROBE = _resolve_binary("ffprobe", settings.FFPROBE_PATH)

# Ensure the ffmpeg dir is on PATH for libraries that shell out (PySceneDetect, OpenCV).
if os.path.isabs(FFMPEG):
    _bindir = str(Path(FFMPEG).parent)
    if _bindir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _bindir + os.pathsep + os.environ.get("PATH", "")

logger.info("real_ops using ffmpeg=%s ffprobe=%s", FFMPEG, FFPROBE)


# ── Metadata ──────────────────────────────────────────────────────────────────

def probe_metadata(path: Path) -> dict:
    """Return {duration_ms, fps, width, height, codec_video, codec_audio, bitrate_kbps}."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout)
        fmt = data.get("format", {})
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})

        fps = None
        rate = v.get("r_frame_rate", "0/1")
        try:
            num, den = rate.split("/")
            fps = round(float(num) / float(den), 2) if float(den) else None
        except Exception:
            pass

        return {
            "duration_ms": int(float(fmt.get("duration", 0)) * 1000) or None,
            "fps": fps,
            "width": int(v["width"]) if v.get("width") else None,
            "height": int(v["height"]) if v.get("height") else None,
            "codec_video": v.get("codec_name"),
            "codec_audio": a.get("codec_name"),
            "bitrate_kbps": int(int(fmt.get("bit_rate", 0)) / 1000) or None,
            "has_audio": bool(a),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("ffprobe failed for %s: %s", path, e)
        return {}


def extract_thumbnail(video: Path, out: Path, at_sec: float = 1.0) -> bool:
    try:
        subprocess.run(
            [FFMPEG, "-y", "-ss", str(at_sec), "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, timeout=30,
        )
        return out.exists()
    except Exception:
        return False


# ── Scene detection (PySceneDetect, CPU) ──────────────────────────────────────

def detect_scenes(video: Path, total_ms: int) -> List[Tuple[int, int]]:
    """Return list of (start_ms, end_ms) shot boundaries."""
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        vid = open_video(str(video))
        mgr = SceneManager()
        mgr.add_detector(ContentDetector(threshold=settings.SCENE_THRESHOLD))
        mgr.detect_scenes(vid, show_progress=False)
        scenes = mgr.get_scene_list()
        boundaries = [
            (int(s[0].get_seconds() * 1000), int(s[1].get_seconds() * 1000))
            for s in scenes
        ]
        if boundaries:
            return boundaries
    except Exception as e:  # noqa: BLE001
        logger.warning("PySceneDetect failed (%s); chunking evenly", e)

    # Fallback: even ~8s chunks
    if total_ms <= 0:
        total_ms = 60_000
    step = 8_000
    return [(c, min(c + step, total_ms)) for c in range(0, total_ms, step)]


def keyframe_and_quality(video: Path, at_ms: int, out: Optional[Path]) -> float:
    """Grab a frame at at_ms, optionally save it, return a 0-1 quality score."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video))
        cap.set(cv2.CAP_PROP_POS_MSEC, at_ms)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return 0.5
        if out is not None:
            # imencode + Python IO instead of cv2.imwrite: imwrite goes through the
            # ANSI code page on Windows and silently fails on some paths.
            ok_enc, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if ok_enc:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(buf.tobytes())
            else:
                logger.warning("keyframe encode failed for %s", video)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = min(cv2.Laplacian(gray, cv2.CV_64F).var() / 500.0, 1.0)
        bright = gray.mean() / 255.0
        exposure = 1.0 - abs(bright - 0.5) * 2
        return float(max(0.0, min(1.0, sharp * 0.6 + exposure * 0.4)))
    except Exception as e:  # noqa: BLE001
        logger.debug("keyframe/quality failed: %s", e)
        return 0.5


# ── Real rendering (ffmpeg) ───────────────────────────────────────────────────

def render_edit(
    segments: List[dict],
    out_path: Path,
    width: int,
    height: int,
    burn_subtitle: Optional[Path] = None,
    normalize_audio: bool = True,
) -> bool:
    """Backward-compatible wrapper around render_edit_ex (returns only success)."""
    return render_edit_ex(segments, out_path, width, height,
                          burn_subtitle=burn_subtitle, normalize_audio=normalize_audio)["ok"]


# Chapters / data tracks / source metadata from phones would otherwise ride
# along into every part and inflate the container duration.
_CLEAN = ["-map_chapters", "-1", "-map_metadata", "-1", "-dn", "-sn"]
_VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", *_CLEAN]
_AENC = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100"]


def _run(cmd: List[str], timeout: int = 600) -> Tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode == 0, r.stderr.decode(errors="replace")[-600:]


def render_edit_ex(
    segments: List[dict],
    out_path: Path,
    width: int,
    height: int,
    burn_subtitle: Optional[Path] = None,
    normalize_audio: bool = True,
    music: Optional[list] = None,
    overlays: Optional[List[dict]] = None,
) -> dict:
    """
    Real ffmpeg render of a timeline.

    segments: [{"src", "start_ms", "end_ms", "effects"?: {...}, "transition_in"?: str}]
    music:    [render_graph.MusicTrack]
    overlays: [{"text", "start_ms", "end_ms", "position", "font_size", "color", "box"}]

    Stages: per-part render (trim/speed/color/scale, exact-length audio) →
    join (xfade transitions or concat) → finish (music mix, ducking, text,
    subtitles, -14 LUFS). Each stage degrades gracefully and records a
    warning instead of failing the whole render.
    Returns {"ok": bool, "warnings": [str], "parts": int}.
    """
    from backend.core import render_graph as rg

    result = {"ok": False, "warnings": [], "parts": 0}
    if not segments:
        return result

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parts: List[Path] = []
        durations: List[float] = []
        transitions: List[str] = []
        audio_cache: dict = {}

        # ── 1. parts ──
        for i, seg in enumerate(segments):
            src = Path(seg["src"])
            if not src.exists():
                result["warnings"].append(f"clip {i + 1}: source file missing")
                continue
            if str(src) not in audio_cache:
                audio_cache[str(src)] = probe_metadata(src).get("has_audio", True)
            has_audio = audio_cache[str(src)]
            effects = seg.get("effects")
            src_dur = max(0.3, (seg["end_ms"] - seg["start_ms"]) / 1000.0)
            out_dur = rg.part_output_duration(seg["start_ms"], seg["end_ms"], effects)
            part = tmp / f"part_{i:03d}.mp4"
            cmd = [FFMPEG, "-y", "-ss", f"{max(0, seg['start_ms']) / 1000.0:.3f}",
                   "-t", f"{src_dur:.3f}", "-i", str(src)]
            if not has_audio:
                cmd += ["-f", "lavfi", "-t", f"{out_dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            fc = rg.build_part_filter(width, height, effects, out_dur, "0:a" if has_audio else "1:a",
                                      crop=seg.get("crop"))
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", *_VENC, *_AENC, str(part)]
            ok, err = _run(cmd, timeout=300)
            if not ok and seg.get("crop"):
                # smart-crop expression rejected → fall back to letterboxing this part
                logger.warning("segment %d crop failed, retrying without: %s", i, err[-300:])
                fc = rg.build_part_filter(width, height, effects, out_dur, "0:a" if has_audio else "1:a")
                cmd[cmd.index("-filter_complex") + 1] = fc
                ok, err = _run(cmd, timeout=300)
                if ok:
                    result["warnings"].append(f"clip {i + 1}: smart reframing failed — letterboxed instead")
            if ok and part.exists():
                parts.append(part)
                durations.append(out_dur)
                transitions.append(seg.get("transition_in") or "cut")
            else:
                logger.warning("segment %d render failed: %s", i, err[-300:])
                result["warnings"].append(f"clip {i + 1}: render failed")

        if not parts:
            return result
        result["parts"] = len(parts)

        # ── 2. join ──
        joined = tmp / "joined.mp4"
        joined_ok = False
        if len(parts) == 1:
            shutil.copy(parts[0], joined)
            joined_ok = True
        elif rg.needs_xfade(transitions):
            plan = rg.build_join_filter(durations, transitions)
            cmd = [FFMPEG, "-y"]
            for p in parts:
                cmd += ["-i", str(p)]
            cmd += ["-filter_complex", plan.filter_complex, "-map", f"[{plan.video_label}]",
                    "-map", f"[{plan.audio_label}]", *_VENC, *_AENC, str(joined)]
            joined_ok, err = _run(cmd)
            if not joined_ok:
                logger.warning("xfade join failed, falling back to hard cuts: %s", err[-300:])
                result["warnings"].append("transitions failed — rendered with hard cuts")
        if not joined_ok:
            listf = tmp / "list.txt"
            listf.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
            joined_ok, err = _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                                   "-c", "copy", *_CLEAN, str(joined)])
            if not joined_ok:
                joined_ok, err = _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                                       *_VENC, *_AENC, str(joined)])
            if not joined_ok:
                logger.error("concat failed: %s", err[-300:])
                return result

        # ── 3. finish ──
        total_sec = (probe_metadata(joined).get("duration_ms") or int(sum(durations) * 1000)) / 1000.0
        music = [m for m in (music or []) if Path(m.path).exists()]
        ov_objs = []
        for j, ov in enumerate(overlays or []):
            tf = tmp / f"text_{j}.txt"
            tf.write_text(str(ov.get("text", "")), encoding="utf-8")
            ov_objs.append(rg.Overlay(
                textfile=tf, start_ms=int(ov["start_ms"]), end_ms=int(ov["end_ms"]),
                position=ov.get("position", "bottom"), font_size=int(ov.get("font_size", 56)),
                color=str(ov.get("color", "white")), box=bool(ov.get("box", True)),
            ))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        font = rg.find_font()

        attempts = [(ov_objs, burn_subtitle)]
        if ov_objs or burn_subtitle:
            attempts.append(([], None))  # retry without text if drawtext/subtitles fails
        for k, (ovs, sub) in enumerate(attempts):
            plan = rg.build_finish_filter(total_sec, music, ovs, width, height, sub, normalize_audio, font)
            cmd = [FFMPEG, "-y", "-i", str(joined), *plan.input_args,
                   "-filter_complex", plan.filter_complex,
                   "-map", plan.video_map, "-map", "[aout]",
                   *(_VENC if plan.reencode_video else ["-c:v", "copy", *_CLEAN]),
                   *_AENC, "-movflags", "+faststart", str(out_path)]
            ok, err = _run(cmd)
            if ok and out_path.exists():
                if k > 0:
                    result["warnings"].append("text overlays/captions failed — rendered without them")
                result["ok"] = True
                return result
            logger.warning("finish pass %d failed: %s", k, err[-300:])

        # Last resort: ship the joined cut without music/overlays.
        shutil.copy(joined, out_path)
        result["warnings"].append("music mix failed — rendered without music")
        result["ok"] = out_path.exists()
        return result


# ── Audio-only loudness normalization (used by re-render / audio-enhancement) ──

def normalize_loudness(input_path: Path, output_path: Path) -> bool:
    """Real ffmpeg loudnorm pass (-14 LUFS) — signal processing, not a model."""
    try:
        r = subprocess.run(
            [FFMPEG, "-y", "-i", str(input_path),
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:v", "copy", "-c:a", "aac", str(output_path)],
            capture_output=True, timeout=300,
        )
        return r.returncode == 0 and output_path.exists()
    except Exception as e:  # noqa: BLE001
        logger.warning("loudnorm failed for %s: %s", input_path, e)
        return False


# ── QA: real integrity + black-frame check (heuristic, not true VMAF) ─────────

def check_output_integrity(path: Path) -> dict:
    """
    Real checks: file decodes, duration > 0, and a black-frame scan via
    ffmpeg's `blackdetect` filter. `heuristic_quality_score` is a bitrate
    bucket, not measured VMAF — libvmaf isn't guaranteed to be present in
    every ffmpeg build, so this is labeled honestly rather than as "VMAF".
    """
    meta = probe_metadata(path)
    duration_ms = meta.get("duration_ms") or 0
    result = {
        "integrity_ok": duration_ms > 0,
        "duration_ms": duration_ms,
        "black_ms": 0,
        "heuristic_quality_score": None,
    }
    if duration_ms <= 0:
        return result

    try:
        r = subprocess.run(
            [FFMPEG, "-i", str(path), "-vf", "blackdetect=d=0.5:pix_th=0.10",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        black_ms = 0
        for match in re.finditer(r"black_duration:([\d.]+)", r.stderr):
            black_ms += int(float(match.group(1)) * 1000)
        result["black_ms"] = black_ms
    except Exception as e:  # noqa: BLE001
        logger.debug("blackdetect failed: %s", e)

    bitrate = meta.get("bitrate_kbps") or 0
    if bitrate >= 2000:
        result["heuristic_quality_score"] = 85.0
    elif bitrate >= 1000:
        result["heuristic_quality_score"] = 78.0
    elif bitrate > 0:
        result["heuristic_quality_score"] = 70.0
    else:
        result["heuristic_quality_score"] = 60.0
    return result


# ── Subtitles ─────────────────────────────────────────────────────────────────

def format_srt_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    hh, rem = divmod(ms, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1_000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def build_srt(cues: List[Tuple[int, int, str]]) -> str:
    """cues: [(start_ms, end_ms, text)] — real cue timing, honest placeholder text."""
    lines = []
    for i, (start_ms, end_ms, text) in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{format_srt_timestamp(start_ms)} --> {format_srt_timestamp(end_ms)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)
