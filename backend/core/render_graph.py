"""
Pure builders for the ffmpeg filter graphs used by real_ops.render_edit_ex.

Kept free of subprocess/IO so every graph can be unit-tested as a string.
Three stages:

1. part     — one timeline entry: trim → speed → color → scale/pad, with a
              normalized stereo 44.1 kHz audio track of *exactly* the video's
              duration (so later joins never drift).
2. join     — all parts, honoring each entry's transition_in: `xfade` +
              `acrossfade` for dissolves/wipes/fades, `concat` for hard cuts.
3. finish   — music beds (offset / loop / fades / ducking), text overlays,
              burned subtitles and the final -14 LUFS loudness normalization.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TRANSITION_SEC = 0.5

# timeline transition → ffmpeg xfade transition name. "cut" is a plain concat.
XFADE_MAP: Dict[str, str] = {
    "dissolve": "dissolve",
    "cross_fade": "fade",
    "fade_to_black": "fadeblack",
    "wipe": "wipeleft",
    "zoom_in": "zoomin",
    "zoom_out": "circleopen",
}

FILTER_PRESETS: Dict[str, str] = {
    "none": "",
    "bw": "hue=s=0",
    "warm": "colorbalance=rs=0.08:gs=0.02:bs=-0.08",
    "cool": "colorbalance=rs=-0.08:gs=0.0:bs=0.08",
    "vivid": "eq=saturation=1.35:contrast=1.08",
    "vintage": "curves=preset=vintage",
    "cinematic": "eq=contrast=1.12:saturation=0.9,colorbalance=rs=-0.04:bs=0.05:rh=0.05:bh=-0.03",
}

DEFAULT_EFFECTS = {
    "speed": 1.0, "volume": 1.0, "muted": False,
    "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "filter": "none",
}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_effects(effects: Optional[dict]) -> dict:
    e = dict(DEFAULT_EFFECTS)
    for k, v in (effects or {}).items():
        if k in e and v is not None:
            e[k] = v
    e["speed"] = clamp(float(e["speed"]), 0.25, 4.0)
    e["volume"] = clamp(float(e["volume"]), 0.0, 2.0)
    e["brightness"] = clamp(float(e["brightness"]), -0.5, 0.5)
    e["contrast"] = clamp(float(e["contrast"]), 0.5, 2.0)
    e["saturation"] = clamp(float(e["saturation"]), 0.0, 3.0)
    e["muted"] = bool(e["muted"])
    if e["filter"] not in FILTER_PRESETS:
        e["filter"] = "none"
    return e


def atempo_chain(speed: float) -> List[str]:
    """atempo only accepts 0.5–2.0 per instance; chain it for 0.25–4x."""
    out: List[str] = []
    s = speed
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-3:
        out.append(f"atempo={s:.4f}")
    return out


def part_output_duration(start_ms: int, end_ms: int, effects: Optional[dict]) -> float:
    e = normalize_effects(effects)
    src_dur = max(0.3, (end_ms - start_ms) / 1000.0)
    return src_dur / e["speed"]


def build_part_filter(width: int, height: int, effects: Optional[dict],
                      out_duration: float, audio_label: str, crop: Optional[str] = None) -> str:
    """
    filter_complex for one part. Input 0 is the source video; `audio_label`
    is either "0:a" (source has audio) or "1:a" (an anullsrc silence input).
    `crop` (smart reframing) runs first, while t is still source time.
    """
    e = normalize_effects(effects)
    v: List[str] = []
    if crop:
        v.append(crop)
    if abs(e["speed"] - 1.0) > 1e-3:
        v.append(f"setpts=PTS/{e['speed']:.4f}")
    if (abs(e["brightness"]) > 1e-3 or abs(e["contrast"] - 1) > 1e-3
            or abs(e["saturation"] - 1) > 1e-3):
        v.append(f"eq=brightness={e['brightness']:.3f}:contrast={e['contrast']:.3f}"
                 f":saturation={e['saturation']:.3f}")
    preset = FILTER_PRESETS.get(e["filter"], "")
    if preset:
        v.append(preset)
    v.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    v.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    v.append("setsar=1,fps=30,format=yuv420p")
    v.append(f"trim=duration={out_duration:.3f},setpts=PTS-STARTPTS")

    a: List[str] = []
    if audio_label == "0:a":
        a.extend(atempo_chain(e["speed"]))
    vol = 0.0 if e["muted"] else e["volume"]
    if abs(vol - 1.0) > 1e-3:
        a.append(f"volume={vol:.3f}")
    a.append("aformat=sample_rates=44100:channel_layouts=stereo")
    # Pad then cut so audio is exactly as long as the video — no drift at joins.
    a.append(f"apad,atrim=duration={out_duration:.3f},asetpts=PTS-STARTPTS")

    return f"[0:v]{','.join(v)}[v];[{audio_label}]{','.join(a)}[a]"


@dataclass
class JoinPlan:
    filter_complex: str
    total_duration: float
    video_label: str = "vout"
    audio_label: str = "aout"


def build_join_filter(durations: List[float], transitions: List[str]) -> JoinPlan:
    """
    Chain N parts (inputs 0..N-1). transitions[i] is the transition INTO part i
    (transitions[0] is ignored). Crossfades overlap the parts, so the total
    duration shrinks by each transition's length.
    """
    assert durations and len(durations) == len(transitions)
    # xfade requires both inputs to share a timebase, but concat outputs a
    # different one than the encoded parts — normalize every video stream
    # (inputs and each intermediate result) to the same timebase.
    tb = "settb=AVTB"
    chains: List[str] = [f"[{i}:v]{tb}[in{i}]" for i in range(len(durations))]
    cur_v, cur_a = "in0", "0:a"
    total = durations[0]
    for i in range(1, len(durations)):
        tname = XFADE_MAP.get(transitions[i] or "cut")
        nv, na = f"v{i}", f"a{i}"
        if tname:
            d = min(TRANSITION_SEC, durations[i - 1] / 2, durations[i] / 2, total / 2)
            d = max(0.05, d)
            offset = max(0.0, total - d)
            chains.append(
                f"[{cur_v}][in{i}]xfade=transition={tname}:duration={d:.3f}:offset={offset:.3f},{tb}[{nv}]")
            chains.append(f"[{cur_a}][{i}:a]acrossfade=d={d:.3f}[{na}]")
            total = total + durations[i] - d
        else:
            chains.append(f"[{cur_v}][{cur_a}][in{i}][{i}:a]concat=n=2:v=1:a=1[c{i}][{na}]")
            chains.append(f"[c{i}]{tb}[{nv}]")
            total += durations[i]
        cur_v, cur_a = nv, na
    if len(durations) == 1:
        chains.append("[in0]null[vout]")
        chains.append("[0:a]anull[aout]")
    else:
        chains.append(f"[{cur_v}]null[vout]")
        chains.append(f"[{cur_a}]anull[aout]")
    return JoinPlan(";".join(chains), total)


def needs_xfade(transitions: List[str]) -> bool:
    return any(XFADE_MAP.get(t or "cut") for t in transitions[1:])


# ── Finishing pass ────────────────────────────────────────────────────────────

@dataclass
class MusicTrack:
    path: Path
    start_ms: int = 0
    source_offset_ms: int = 0
    length_ms: Optional[int] = None
    volume: float = 0.8
    fade_in_ms: int = 500
    fade_out_ms: int = 1500
    loop: bool = False
    duck_original: float = 1.0


@dataclass
class Overlay:
    textfile: Path
    start_ms: int
    end_ms: int
    position: str = "bottom"
    font_size: int = 56
    color: str = "white"
    box: bool = True


@dataclass
class FinishPlan:
    input_args: List[str] = field(default_factory=list)
    filter_complex: str = ""
    reencode_video: bool = False
    video_map: str = "0:v"   # "[vout]" when the video is filtered, else stream-copied


def ffmpeg_path_escape(p: Path) -> str:
    """Escape a filesystem path for use inside a filter option value."""
    return p.as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def music_window(track: MusicTrack, total_sec: float) -> Tuple[float, float]:
    """(start_sec, length_sec) of a music bed, clipped to the video length."""
    start = clamp(track.start_ms / 1000.0, 0.0, max(0.0, total_sec))
    avail = max(0.0, total_sec - start)
    length = avail if track.length_ms is None else min(avail, track.length_ms / 1000.0)
    return start, length


def build_finish_filter(total_sec: float, music: List[MusicTrack], overlays: List[Overlay],
                        width: int, height: int, subtitle: Optional[Path],
                        normalize_audio: bool, fontfile: Optional[Path]) -> FinishPlan:
    """Input 0 is the joined video; music tracks are inputs 1..N (added by the caller)."""
    plan = FinishPlan()
    parts: List[str] = []

    # ── video: overlays + subtitles ──
    vchain: List[str] = []
    for ov in overlays:
        s, e = ov.start_ms / 1000.0, ov.end_ms / 1000.0
        if e <= s:
            continue
        size = max(12, int(ov.font_size * height / 1080))
        y = {"top": "h*0.08", "center": "(h-text_h)/2"}.get(ov.position, "h*0.86-text_h")
        opts = [
            f"textfile='{ffmpeg_path_escape(ov.textfile)}'",
            f"fontsize={size}", f"fontcolor={ov.color}",
            "x=(w-text_w)/2", f"y={y}",
            f"enable='between(t,{s:.3f},{e:.3f})'",
            "borderw=2:bordercolor=black@0.6",
        ]
        if fontfile:
            opts.insert(0, f"fontfile='{ffmpeg_path_escape(fontfile)}'")
        if ov.box:
            opts.append("box=1:boxcolor=black@0.45:boxborderw=18")
        vchain.append("drawtext=" + ":".join(opts))
    if subtitle and subtitle.exists():
        sub = f"subtitles='{ffmpeg_path_escape(subtitle)}'"
        if fontfile:
            sub += f":fontsdir='{ffmpeg_path_escape(fontfile.parent)}'"
        vchain.append(sub)
    if vchain:
        parts.append(f"[0:v]{','.join(vchain)}[vout]")
        plan.reencode_video = True
        plan.video_map = "[vout]"

    # ── audio: duck original under music, mix, normalize ──
    duck = []
    for t in music:
        if t.duck_original < 0.999:
            s, ln = music_window(t, total_sec)
            if ln > 0:
                duck.append(f"volume=enable='between(t,{s:.3f},{s + ln:.3f})':volume={t.duck_original:.3f}")
    parts.append(f"[0:a]{','.join(duck) if duck else 'anull'}[orig]")

    mix_labels = ["[orig]"]
    i = 0  # ffmpeg input index of the next music bed (input 0 is the video)
    for t in music:
        s, ln = music_window(t, total_sec)
        if ln <= 0.05:
            continue
        i += 1
        fi = min(t.fade_in_ms / 1000.0, ln / 2)
        fo = min(t.fade_out_ms / 1000.0, ln / 2)
        chain = [
            f"atrim=start={t.source_offset_ms / 1000.0:.3f}:duration={ln:.3f}",
            "asetpts=PTS-STARTPTS",
            "aformat=sample_rates=44100:channel_layouts=stereo",
            f"volume={clamp(t.volume, 0.0, 2.0):.3f}",
        ]
        if fi > 0:
            chain.append(f"afade=t=in:st=0:d={fi:.3f}")
        if fo > 0:
            chain.append(f"afade=t=out:st={max(0.0, ln - fo):.3f}:d={fo:.3f}")
        delay = int(s * 1000)
        if delay > 0:
            chain.append(f"adelay={delay}|{delay}")
        parts.append(f"[{i}:a]{','.join(chain)}[m{i}]")
        mix_labels.append(f"[m{i}]")
        if t.loop:
            plan.input_args += ["-stream_loop", "-1"]
        plan.input_args += ["-i", str(t.path)]

    tail = "loudnorm=I=-14:TP=-1.5:LRA=11" if normalize_audio else "anull"
    if len(mix_labels) > 1:
        parts.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,{tail}[aout]")
    else:
        parts.append(f"[orig]{tail}[aout]")
    plan.filter_complex = ";".join(parts)
    return plan


def find_font() -> Optional[Path]:
    for cand in (
        "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
    ):
        if Path(cand).exists():
            return Path(cand)
    return None
