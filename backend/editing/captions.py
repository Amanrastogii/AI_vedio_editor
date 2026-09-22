"""
Transcript words → timed caption cues that follow the edit → SRT / VTT / styled ASS.

Captions are derived from the timeline every time (never stored), so trimming,
reordering, speed changes or silence removal can't leave them out of sync.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.editing.layout import Placed

PUNCT_END = re.compile(r"[.!?…]$")
MAX_CUE_MS = 3500
GAP_BREAK_MS = 700
MIN_CUE_MS = 500


@dataclass
class CueWord:
    text: str
    start: float           # sequence ms
    end: float
    word_id: Optional[str] = None


@dataclass
class Cue:
    start: float
    end: float
    words: List[CueWord] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict:
        return {"start_ms": int(self.start), "end_ms": int(self.end), "text": self.text,
                "words": [{"text": w.text, "start_ms": int(w.start), "end_ms": int(w.end), "id": w.word_id}
                          for w in self.words]}


def sequence_words(placed: List[Placed], words_by_clip: Dict[str, List[dict]], hide_fillers: bool) -> List[CueWord]:
    """Words visible in the edit, in sequence time. Crossfade overlaps split at their midpoint."""
    out: List[CueWord] = []
    for i, p in enumerate(placed):
        vis_start = p.start + (p.overlap_in / 2 if i > 0 else 0)
        nxt = placed[i + 1] if i + 1 < len(placed) else None
        vis_end = p.end - (nxt.overlap_in / 2 if nxt else 0)
        for w in words_by_clip.get(p.clip_id or "", []):
            if w.get("is_silence") or (hide_fillers and w.get("is_filler")):
                continue
            if w["end_ms"] <= p.src_in or w["start_ms"] >= p.src_out:
                continue
            s = max(vis_start, p.src_to_seq(max(w["start_ms"], p.src_in)))
            e = min(vis_end, p.src_to_seq(min(w["end_ms"], p.src_out)))
            # keep a word only if most of it survives the cut
            if e - s < 0.5 * (w["end_ms"] - w["start_ms"]) / p.speed or e <= s:
                continue
            out.append(CueWord(w["word"].strip(), s, e, w.get("id")))
    out.sort(key=lambda w: w.start)
    return out


def _wrap(words: List[str], max_chars: int) -> List[str]:
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def build_cues(words: List[CueWord], max_chars: int = 32, max_lines: int = 2, uppercase: bool = False) -> List[Cue]:
    cues: List[Cue] = []
    cur: List[CueWord] = []

    def flush():
        if not cur:
            return
        texts = [w.text.upper() if uppercase else w.text for w in cur]
        cues.append(Cue(cur[0].start, cur[-1].end, list(cur), _wrap(texts, max_chars)))
        cur.clear()

    budget = max_chars * max_lines
    for w in words:
        if cur:
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            if (chars > budget or w.start - cur[-1].end > GAP_BREAK_MS
                    or w.end - cur[0].start > MAX_CUE_MS or PUNCT_END.search(cur[-1].text)):
                flush()
        cur.append(w)
    flush()
    # readable minimum duration; hold a cue until the next one (up to 300 ms)
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else None
        target = max(c.end + 300, c.start + MIN_CUE_MS)
        c.end = min(target, nxt) if nxt is not None else target
    return cues


# ── writers ───────────────────────────────────────────────────────────────────

def _srt_ts(ms: float) -> str:
    ms = max(0, int(ms))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cues: List[Cue]) -> str:
    return "\n".join(f"{i}\n{_srt_ts(c.start)} --> {_srt_ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))


def to_vtt(cues: List[Cue]) -> str:
    body = "\n".join(f"{_srt_ts(c.start).replace(',', '.')} --> {_srt_ts(c.end).replace(',', '.')}\n{c.text}\n"
                     for c in cues)
    return "WEBVTT\n\n" + body


def _ass_ts(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(cues: List[Cue], cfg: dict, width: int, height: int, font: str = "Arial") -> str:
    """Styled ASS. Presets: classic (boxed), bold (outlined + active-word highlight),
    karaoke (word-by-word fill sweep), minimal (small, soft shadow)."""
    preset = cfg.get("preset", "bold")
    # font_size is defined for a 1080-px short side, so a 9:16 reel and a 16:9 video get the same
    # relative size (scaling by height made vertical captions ~2x too big and overflow the frame)
    size = int(cfg.get("font_size", 64) * min(width, height) / 1080)
    text_c = _ass_color(cfg.get("text_color", "#FFFFFF"))
    hi_c = _ass_color(cfg.get("highlight_color", "#FFD400"))
    align = {"top": 8, "center": 5}.get(cfg.get("position", "bottom"), 2)
    margin_v = int(height * (0.07 if align == 8 else 0.12 if align == 2 else 0))
    margin_h = int(width * 0.06)
    if preset == "classic":
        style = f"{font},{size},{text_c},{text_c},&H00000000,&H78000000,0,0,0,0,100,100,0,0,3,{max(2, size // 7)},0"
    elif preset == "minimal":
        style = f"{font},{int(size * 0.8)},{text_c},{text_c},&H64000000,&H64000000,0,0,0,0,100,100,0,0,1,1,{max(1, size // 30)}"
    elif preset == "karaoke":
        style = f"{font},{size},{hi_c},{text_c},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, size // 14)},0"
    else:  # bold
        style = f"{font},{size},{text_c},{text_c},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(3, size // 11)},0"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Cap,{style},{align},{margin_h},{margin_h},{margin_v},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    upper = cfg.get("uppercase", False)
    # characters that physically fit on one line at this size (bold sans ≈ 0.55 em per char;
    # caps are wider), so narrow vertical frames wrap instead of overflowing
    em = 0.62 if upper else 0.55
    fit = int((width - 2 * margin_h) / max(1.0, em * (size * (0.8 if preset == "minimal" else 1.0))))
    max_chars = max(8, min(int(cfg.get("max_chars", 32)), fit))

    def render_words(active: Optional[int], c: Cue) -> str:
        """Cue text with line breaks where _wrap put them; optional highlighted word."""
        parts, line_len = [], 0
        for j, w in enumerate(c.words):
            t = _ass_escape(w.text.upper() if upper else w.text)
            sep = ""
            if j:
                if line_len + 1 + len(t) > max_chars:
                    sep, line_len = "\\N", 0
                else:
                    sep, line_len = " ", line_len + 1
            line_len += len(t)
            parts.append(sep + (f"{{\\c{hi_c}}}{t}{{\\c{text_c}}}" if j == active else t))
        return "".join(parts)

    for c in cues:
        if preset == "bold" and c.words:
            # one event per spoken word: same text, the active word highlighted
            for j, w in enumerate(c.words):
                s = c.start if j == 0 else w.start
                e = c.words[j + 1].start if j + 1 < len(c.words) else c.end
                if e - s < 20:
                    continue
                lines.append(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(e)},Cap,,0,0,0,,{render_words(j, c)}")
        elif preset == "karaoke" and c.words:
            k_parts, line_len, prev = [], 0, c.start
            for j, w in enumerate(c.words):
                t = _ass_escape(w.text.upper() if upper else w.text)
                sep = ""
                if j:
                    if line_len + 1 + len(t) > max_chars:
                        sep, line_len = "\\N", 0
                    else:
                        sep, line_len = " ", line_len + 1
                line_len += len(t)
                lead = max(0, int((w.start - prev) / 10))
                dur = max(1, int((w.end - w.start) / 10))
                k_parts.append(f"{sep}{{\\k{lead}}}{{\\kf{dur}}}{t}")
                prev = w.end
            lines.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Cap,,0,0,0,,{''.join(k_parts)}")
        else:
            lines.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Cap,,0,0,0,,{render_words(None, c)}")
    return "\n".join(lines) + "\n"
