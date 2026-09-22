"""
Natural-language editing commands.

    parse_rules(text)      → [action]   deterministic grammar, no model needed
    ClaudeInterpreter      → [action]   Claude tool-use (when ANTHROPIC_API_KEY is set)
    execute(pid, actions)  → reply      one executor for both, with validation

An action is a plain dict {"type": ..., ...}. Clips are addressed by timeline
position (1-based) or "all".
"""
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.ai.base import CommandInterpreterProvider, InterpretResult
from backend.config import settings
from backend.core.render_graph import FILTER_PRESETS, normalize_effects
from backend.database.db import AsyncSessionLocal
from backend.database.models import TransitionType
from backend.database.repositories import StoryTimelineRepository, StyleRepository, TextOverlayRepository
from backend.editing.layout import place, rows_from_entries, total_ms

logger = logging.getLogger(__name__)

Targets = Union[str, List[int]]

HELP = (
    "Try: \"remove clip 2\" · \"move clip 3 to the start\" · \"split clip 2 at 3s\" · \"duplicate clip 1\" · "
    "\"speed up clip 2 to 2x\" · \"slow down clip 3\" · \"mute clip 4\" · \"volume of clip 2 to 50%\" · "
    "\"make clip 1 black and white\" · \"warm filter on all clips\" · \"brighten clip 2\" · "
    "\"use dissolve transitions everywhere\" · \"add text 'Day 1' at 2s for 3s\" · \"add captions\" · "
    "\"karaoke captions\" · \"remove silences and filler words\" · \"sync cuts to the beat\" · "
    "\"make it 30 seconds\" · \"smart crop for vertical\" · \"apply style Travel\" · \"render\"."
)

TRANSITION_WORDS = {
    "cut": "cut", "hard cut": "cut", "dissolve": "dissolve", "fade": "fade_to_black",
    "fade to black": "fade_to_black", "fades": "fade_to_black", "wipe": "wipe", "wipes": "wipe",
    "zoom in": "zoom_in", "zoom out": "zoom_out", "zoom": "zoom_in",
    "cross fade": "cross_fade", "crossfade": "cross_fade", "cross-fade": "cross_fade", "crossfades": "cross_fade",
}
FILTER_WORDS = {
    "black and white": "bw", "black & white": "bw", "b&w": "bw", "bw": "bw", "grayscale": "bw", "greyscale": "bw",
    "monochrome": "bw", "warm": "warm", "warmer": "warm", "cool": "cool", "cooler": "cool", "cold": "cool",
    "vivid": "vivid", "vibrant": "vivid", "vintage": "vintage", "retro": "vintage", "cinematic": "cinematic",
    "film": "cinematic", "no filter": "none", "normal": "none", "none": "none",
}
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


# ── parsing helpers ───────────────────────────────────────────────────────────

def _num(s: str) -> Optional[float]:
    s = s.strip().lower()
    if s in WORD_NUM:
        return float(WORD_NUM[s])
    try:
        return float(s)
    except ValueError:
        return None


def parse_targets(text: str) -> Optional[Targets]:
    t = text.lower()
    if re.search(r"\b(all|every|each)\s+(the\s+)?(clips?|shots?|scenes?)\b|\beverywhere\b|\bwhole (video|thing|edit)\b"
                 r"|\bentire (video|edit)\b|\ball of them\b", t):
        return "all"
    # "clips 2-4" / "clips 2 to 4" — but not "clip 2 to 50%" / "to 2x" / "to 5s"
    m = re.search(r"\b(?:clips?|shots?|scenes?)\s+(\d+)\s*(?:-|to|through|–)\s*(\d+)(?![\d.])(?!\s*(?:%|x\b|s\b|sec))", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return list(range(min(a, b), max(a, b) + 1))
    m = re.search(r"\b(?:clips?|shots?|scenes?)\s+((?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
                  r"(?:\s*(?:,|and|&)\s*(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten))*)", t)
    if m:
        nums = [_num(x) for x in re.split(r"\s*(?:,|and|&)\s*", m.group(1))]
        return [int(n) for n in nums if n]
    m = re.search(r"\b(first|second|third|fourth|fifth|last)\s+(?:clip|shot|scene)\b", t)
    if m:
        return [-1] if m.group(1) == "last" else [WORD_NUM[m.group(1)]]
    return None


def _seconds(text: str, key: str = "at") -> Optional[float]:
    m = re.search(rf"\b{key}\s+(\d+(?:\.\d+)?)\s*(?:s\b|sec|secs|seconds?)?", text)
    return float(m.group(1)) if m else None


def _quoted(text: str) -> Optional[str]:
    m = re.search(r"[\"“'‘](.+?)[\"”'’](?=\s|$|[.,!?])", text)
    return m.group(1).strip() if m else None


def parse_rules(message: str) -> Optional[List[Dict[str, Any]]]:
    """Deterministic grammar. Returns None when nothing matched."""
    actions: List[Dict[str, Any]] = []
    for part in re.split(r"\s*(?:;|\bthen\b|\band then\b)\s*", message.strip()):
        a = _parse_one(part)
        if a is None:
            return None if not actions else actions
        actions.append(a)
    return actions or None


def _parse_one(raw: str) -> Optional[Dict[str, Any]]:
    t = raw.strip().lower().rstrip(".!")
    if not t:
        return None
    if t in ("help", "what can you do", "commands", "?"):
        return {"type": "help"}
    targets = parse_targets(t)

    # render / export
    if re.fullmatch(r"(please\s+)?(render|export|re-?render)( (it|the video|now|again))?", t):
        return {"type": "render"}
    # captions
    if re.search(r"\b(captions?|subtitles?|subs)\b", t):
        if re.search(r"\b(off|remove|disable|hide|no)\b", t):
            return {"type": "captions", "enabled": False}
        preset = next((p for p in ("karaoke", "bold", "classic", "minimal") if p in t), None)
        a = {"type": "captions", "enabled": True, "generate": True}
        if preset:
            a["preset"] = preset
        if "upper" in t or "all caps" in t:
            a["uppercase"] = True
        pos = next((p for p in ("top", "center", "middle", "bottom") if re.search(rf"\b{p}\b", t)), None)
        if pos:
            a["position"] = "center" if pos == "middle" else pos
        return a
    # silence / filler cleanup
    sil = re.search(r"\b(silences?|pauses?|dead air|gaps?)\b", t)
    fil = re.search(r"\b(fillers?|filler words|ums?|uhs?|um and uh|ums and uhs)\b", t)
    if (sil or fil) and re.search(r"\b(remove|cut|delete|trim|drop|get rid of|clean|tighten)\b", t):
        return {"type": "cleanup", "silence": bool(sil), "fillers": bool(fil)}
    if re.search(r"\bjump ?cuts?\b|\btighten (it|the edit|up)\b", t):
        return {"type": "cleanup", "silence": True, "fillers": True}
    # beat sync
    if re.search(r"\b(beat|beats|rhythm|tempo|bpm)\b", t) and re.search(r"\b(sync|cut|snap|match|align|on)\b", t):
        every = 4 if re.search(r"\b(bar|bars|measure)\b", t) else None
        m = re.search(r"every\s+(\d+|two|four|eight)\s+beats?", t)
        if m:
            every = int(_num(m.group(1)) or 1)
        elif re.search(r"every beat", t):
            every = 1
        return {"type": "beat_sync", "every": every}
    # reframe
    if re.search(r"\b(smart ?crop|reframe|follow the (subject|person|face)|auto ?frame|make it vertical)\b", t):
        return {"type": "reframe", "mode": "smart"}
    if re.search(r"\bcent(er|re) crop\b", t):
        return {"type": "reframe", "mode": "center"}
    if re.search(r"\b(letterbox|black bars|no crop|fit (the )?(whole )?frame)\b", t):
        return {"type": "reframe", "mode": "fit"}
    # duration
    m = re.search(r"\b(?:make it|shorten(?: it)?(?: to)?|cut it (?:down )?to|keep it|trim it to|under|to)\s+"
                  r"(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?)\b(?: long)?", t)
    if m and re.search(r"\b(make it|shorten|cut it|keep it|trim it|under|long|total)\b", t) and targets is None:
        v = float(m.group(1)) * (60 if m.group(2).startswith("m") else 1)
        return {"type": "fit_duration", "seconds": v}
    # style
    m = re.search(r"\b(?:apply|use)\s+(?:my\s+|the\s+)?(?:style\s+)?[\"“']?(.+?)[\"”']?\s+style$|"
                  r"\b(?:apply|use)\s+style\s+[\"“']?(.+?)[\"”']?$|\bedit (?:it )?like\s+[\"“']?(.+?)[\"”']?$", t)
    if m:
        return {"type": "apply_style", "name": next(g for g in m.groups() if g).strip()}
    # text overlay
    if re.search(r"\badd (a )?(text|title|caption text|label|heading)\b", t):
        text = _quoted(raw) or re.sub(r"^.*?\badd (?:a )?(?:text|title|label|heading)\s*(?:saying|that says|:)?\s*",
                                      "", raw, flags=re.I)
        text = re.sub(r"\s+(at|for|on top|at the (top|bottom|center))\b.*$", "", text, flags=re.I).strip(" \"'“”")
        if not text:
            return None
        pos = "top" if re.search(r"\btop\b", t) else "center" if re.search(r"\b(center|middle)\b", t) else "bottom"
        return {"type": "add_text", "text": text[:300], "at_seconds": _seconds(t, "at") or 0.0,
                "duration_seconds": _seconds(t, "for") or 3.0, "position": pos}
    # split
    m = re.search(r"\b(?:split|cut|slice|razor)\b.*?\bat\s+(\d+(?:\.\d+)?)\s*s?", t)
    if m and targets and targets != "all":
        return {"type": "split", "clip": targets[0], "at_seconds": float(m.group(1))}
    # remove
    if re.search(r"\b(remove|delete|drop|get rid of|cut out)\b", t) and targets is not None and "filter" not in t:
        return {"type": "remove", "clips": targets}
    # move
    m = re.search(r"\bmove\b.*?\bto\s+(?:position\s+)?(\d+|the (start|beginning|front|end))", t)
    if m and targets and targets != "all":
        if m.group(2):
            dest = 1 if m.group(2) in ("start", "beginning", "front") else 10_000
        else:
            dest = int(m.group(1))
        return {"type": "move", "clip": targets[0], "to": dest}
    # trim
    m = re.search(r"\btrim\b.*?\b(start|end|beginning|in|out)\b.*?\bto\s+(\d+(?:\.\d+)?)\s*s", t)
    if m and targets and targets != "all":
        edge = "start" if m.group(1) in ("start", "beginning", "in") else "end"
        return {"type": "trim", "clip": targets[0], "edge": edge, "seconds": float(m.group(2))}
    # duplicate
    if re.search(r"\b(duplicate|copy|repeat)\b", t) and targets and targets != "all":
        return {"type": "duplicate", "clip": targets[0]}
    # transitions
    if re.search(r"\btransitions?\b|\bbetween (all )?(the )?clips\b", t):
        word = next((w for w in sorted(TRANSITION_WORDS, key=len, reverse=True) if re.search(rf"\b{w}\b", t)), None)
        if word:
            return {"type": "transition", "clips": targets or "all", "value": TRANSITION_WORDS[word]}
    # speed
    m = re.search(r"(\d+(?:\.\d+)?)\s*x\b", t)
    if re.search(r"\bspeed up\b|\bfaster\b|\bslow( it)? down\b|\bslower\b|\bslow[- ]?mo(tion)?\b|\bspeed\b", t) or (
            m and targets):
        if m:
            v = float(m.group(1))
        elif re.search(r"slow|slower", t):
            v = 0.5
        else:
            v = 1.5
        if re.search(r"\b(normal|regular|real[- ]?time) speed\b", t):
            v = 1.0
        return {"type": "speed", "clips": targets or "all", "value": v}
    # mute / volume
    if re.search(r"\bunmute\b", t):
        return {"type": "mute", "clips": targets or "all", "muted": False}
    if re.search(r"\bmute\b|\bsilence (clip|the)\b", t):
        return {"type": "mute", "clips": targets or "all", "muted": True}
    m = re.search(r"\bvolume\b.*?(\d+)\s*%|(\d+)\s*%\s*volume", t)
    if m:
        return {"type": "volume", "clips": targets or "all", "percent": float(m.group(1) or m.group(2))}
    if re.search(r"\b(louder|quieter|softer)\b", t):
        return {"type": "volume", "clips": targets or "all", "relative": 1.5 if "louder" in t else 0.6}
    # filters
    word = next((w for w in sorted(FILTER_WORDS, key=len, reverse=True) if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t)), None)
    if word and (targets is not None or re.search(r"\b(filter|look|make it|grade|everything)\b", t)):
        if not (word in ("warm", "cool") and re.search(r"\b(bright|dark|contrast|saturat)", t)):
            return {"type": "filter", "clips": targets or "all", "name": FILTER_WORDS[word]}
    # color adjustments
    for prop, up, down in (("brightness", r"brighten|brighter|more bright", r"darken|darker|less bright"),
                           ("contrast", r"more contrast|increase contrast|punchier", r"less contrast|decrease contrast|flatter"),
                           ("saturation", r"more saturat\w*|increase saturat\w*|more colou?rful|boost colou?rs?",
                            r"less saturat\w*|decrease saturat\w*|desaturate|muted colou?rs")):
        if re.search(up, t):
            return {"type": "adjust", "clips": targets or "all", "property": prop, "delta": 1}
        if re.search(down, t):
            return {"type": "adjust", "clips": targets or "all", "property": prop, "delta": -1}
    return None


# ── execution ─────────────────────────────────────────────────────────────────

class CommandError(ValueError):
    pass


def _resolve(targets: Targets, count: int) -> List[int]:
    if targets == "all":
        return list(range(1, count + 1))
    out = []
    for p in targets if isinstance(targets, list) else [targets]:
        p = int(p)
        p = count if p == -1 else p
        if not 1 <= p <= count:
            raise CommandError(f"There's no clip {p} — the timeline has {count} clip(s).")
        out.append(p)
    return sorted(set(out))


def _clip_list(ps: List[int], count: int) -> str:
    if len(ps) == count and count > 1:
        return "all clips"
    return ("clip " if len(ps) == 1 else "clips ") + ", ".join(map(str, ps))


async def execute(project_id: str, actions: List[Dict[str, Any]]) -> Tuple[str, Optional[Dict]]:
    """Run actions in order. Returns (reply, action_json) — action_json non-null when the timeline changed."""
    pid = uuid.UUID(project_id)
    replies, changed = [], False
    for a in actions:
        try:
            reply, did_change = await _run(pid, a)
        except CommandError as e:
            replies.append(str(e))
            break
        replies.append(reply)
        changed = changed or did_change
    return " ".join(replies), ({"actions": actions} if changed else None)


async def _set_effects(pid, positions: List[int], fn) -> None:
    async with AsyncSessionLocal() as s:
        repo = StoryTimelineRepository(s)
        tl = await repo.get_ordered(pid)
        for p in positions:
            e = tl[p - 1]
            fx = normalize_effects(e.effects)
            fn(fx)
            await repo.update_entry(e.id, effects=normalize_effects(fx))


async def _run(pid: uuid.UUID, a: Dict[str, Any]) -> Tuple[str, bool]:
    from backend.editing import service as editing
    kind = a.get("type")
    if kind == "help":
        return HELP, False

    async with AsyncSessionLocal() as s:
        tl = await StoryTimelineRepository(s).get_ordered(pid)
    n = len(tl)
    needs_clips = kind not in ("captions", "reframe", "apply_style", "render", "add_text", "help")
    if needs_clips and n == 0:
        raise CommandError("The timeline is empty — add some clips first.")

    if kind == "remove":
        ps = _resolve(a.get("clips", []), n)
        if len(ps) == n:
            raise CommandError("That would remove every clip — I left the timeline as it is.")
        async with AsyncSessionLocal() as s:
            repo = StoryTimelineRepository(s)
            for p in sorted(ps, reverse=True):
                await repo.delete_entry(tl[p - 1].id)
            await repo.reorder(pid, [e.id for e in await repo.get_ordered(pid)])
        return f"Removed {_clip_list(ps, n)}.", True

    if kind == "move":
        src = _resolve([a["clip"]], n)[0]
        dst = max(1, min(n, int(a["to"])))
        ids = [e.id for e in tl]
        moving = ids.pop(src - 1)
        ids.insert(dst - 1, moving)
        async with AsyncSessionLocal() as s:
            await StoryTimelineRepository(s).reorder(pid, ids)
        return f"Moved clip {src} to position {dst}.", True

    if kind in ("trim", "split", "duplicate"):
        p = _resolve([a["clip"]], n)[0]
        e = tl[p - 1]
        seg = e.segment
        ts = e.trim_start_ms if e.trim_start_ms is not None else seg.start_ms
        te = e.trim_end_ms if e.trim_end_ms is not None else seg.end_ms
        async with AsyncSessionLocal() as s:
            repo = StoryTimelineRepository(s)
            if kind == "trim":
                target = max(seg.start_ms, min(seg.end_ms, seg.start_ms + int(float(a["seconds"]) * 1000)))
                if a.get("edge") == "start":
                    target = min(target, te - 200)
                    await repo.update_entry(e.id, trim_start_ms=target)
                else:
                    target = max(target, ts + 200)
                    await repo.update_entry(e.id, trim_end_ms=target)
                return f"Trimmed clip {p}'s {a.get('edge', 'end')} to {(target - seg.start_ms) / 1000:.1f}s.", True
            if kind == "split":
                speed = normalize_effects(e.effects)["speed"]
                at = ts + int(float(a["at_seconds"]) * 1000 * speed)
                if not ts + 200 <= at <= te - 200:
                    raise CommandError(f"Clip {p} is {(te - ts) / speed / 1000:.1f}s long — pick a split point inside it.")
                await repo.update_entry(e.id, trim_end_ms=at)
                await repo.insert_after(pid, e.id, e.segment_id, trim_start_ms=at, trim_end_ms=te,
                                        narrative_role=e.narrative_role, transition_in=TransitionType.CUT,
                                        effects=e.effects, edit_reasoning=e.edit_reasoning, reframe_params=e.reframe_params)
                return f"Split clip {p} at {float(a['at_seconds']):.1f}s.", True
            await repo.insert_after(pid, e.id, e.segment_id, trim_start_ms=e.trim_start_ms, trim_end_ms=e.trim_end_ms,
                                    narrative_role=e.narrative_role, transition_in=e.transition_in,
                                    effects=e.effects, edit_reasoning=e.edit_reasoning, reframe_params=e.reframe_params)
            return f"Duplicated clip {p}.", True

    if kind == "transition":
        value = a.get("value")
        if value not in {t.value for t in TransitionType}:
            raise CommandError(f"Unknown transition '{value}'.")
        ps = [p for p in _resolve(a.get("clips", "all"), n) if p > 1 or value == "cut"]
        async with AsyncSessionLocal() as s:
            repo = StoryTimelineRepository(s)
            for p in ps:
                await repo.update_entry(tl[p - 1].id, transition_in=TransitionType(value))
        return f"Set the transition into {_clip_list(ps, n)} to {value.replace('_', ' ')}.", True

    if kind == "speed":
        v = float(a["value"])
        if not 0.25 <= v <= 4:
            raise CommandError("Speed must be between 0.25x and 4x.")
        ps = _resolve(a.get("clips", "all"), n)
        await _set_effects(pid, ps, lambda fx: fx.update(speed=v))
        return f"Set {_clip_list(ps, n)} to {v:g}x speed.", True

    if kind == "mute":
        ps = _resolve(a.get("clips", "all"), n)
        await _set_effects(pid, ps, lambda fx: fx.update(muted=bool(a.get("muted", True))))
        return f"{'Muted' if a.get('muted', True) else 'Unmuted'} {_clip_list(ps, n)}.", True

    if kind == "volume":
        ps = _resolve(a.get("clips", "all"), n)
        if a.get("percent") is not None:
            v = max(0.0, min(200.0, float(a["percent"]))) / 100
            await _set_effects(pid, ps, lambda fx: fx.update(volume=v, muted=False))
            return f"Set the volume of {_clip_list(ps, n)} to {v * 100:.0f}%.", True
        r = float(a.get("relative", 1.0))
        await _set_effects(pid, ps, lambda fx: fx.update(volume=min(2.0, fx["volume"] * r), muted=False))
        return f"Made {_clip_list(ps, n)} {'louder' if r > 1 else 'quieter'}.", True

    if kind == "filter":
        name = a.get("name")
        if name not in FILTER_PRESETS:
            raise CommandError(f"Unknown filter '{name}'. Options: {', '.join(FILTER_PRESETS)}.")
        ps = _resolve(a.get("clips", "all"), n)
        await _set_effects(pid, ps, lambda fx: fx.update(filter=name))
        label = {"bw": "black & white", "none": "no filter"}.get(name, name)
        return f"Applied {label} to {_clip_list(ps, n)}.", True

    if kind == "adjust":
        prop = a.get("property")
        step = {"brightness": 0.08, "contrast": 0.12, "saturation": 0.25}.get(prop)
        if step is None:
            raise CommandError(f"I can adjust brightness, contrast or saturation — not '{prop}'.")
        d = step * (1 if float(a.get("delta", 1)) > 0 else -1)
        ps = _resolve(a.get("clips", "all"), n)
        await _set_effects(pid, ps, lambda fx: fx.update({prop: fx[prop] + d}))
        return f"{'Increased' if d > 0 else 'Decreased'} {prop} on {_clip_list(ps, n)}.", True

    if kind == "add_text":
        start = int(float(a.get("at_seconds") or 0) * 1000)
        dur = int(float(a.get("duration_seconds") or 3) * 1000)
        pos = a.get("position") if a.get("position") in ("top", "center", "bottom") else "bottom"
        async with AsyncSessionLocal() as s:
            await TextOverlayRepository(s).create(pid, text=str(a["text"])[:300], start_ms=start, end_ms=start + max(300, dur),
                                                  position=pos, font_size=56, color="#ffffff", box=True)
        return f"Added text “{a['text']}” at {start / 1000:.1f}s for {dur / 1000:.1f}s ({pos}).", True

    if kind == "captions":
        patch = {k: a[k] for k in ("enabled", "preset", "uppercase", "position") if k in a}
        await editing.update_settings(pid, {"captions": patch})
        if a.get("enabled") is False:
            return "Captions turned off.", True
        status = await editing.transcription_status(pid)
        if not status["available"]:
            return "Captions are on, but speech-to-text isn't installed (pip install faster-whisper).", True
        if a.get("generate") and status["transcribed"] < status["clips"]:
            editing.start_job(str(pid), "transcribe",
                              lambda progress: editing.ensure_transcripts(pid, progress=progress))
            return (f"Captions on{' (' + a['preset'] + ')' if a.get('preset') else ''} — transcribing "
                    f"{status['clips'] - status['transcribed']} clip(s) now; they'll appear when it's done."), True
        return f"Captions on{' (' + a['preset'] + ' style)' if a.get('preset') else ''}.", True

    if kind == "cleanup":
        from backend.editing.cleanup import CleanupOptions
        status = await editing.transcription_status(pid)
        if status["available"] and status["transcribed"] < status["clips"]:
            await editing.ensure_transcripts(pid)
        opts = CleanupOptions(remove_silence=bool(a.get("silence", True)), remove_fillers=bool(a.get("fillers", True)))
        _, plan = await editing.plan_project_cleanup(pid, opts)
        if plan.removed_ms <= 0:
            return "Nothing to remove — no long silences or filler words found in the clips with speech.", False
        await editing.apply_rows(pid, plan.rows, "Before removing silences/fillers (auto-saved)")
        return (f"Removed {plan.removed_ms / 1000:.1f}s ({plan.silences_removed} silences, "
                f"{plan.fillers_removed} filler words) with {plan.cuts_added} jump cuts. "
                f"The previous cut is saved in Versions."), True

    if kind == "beat_sync":
        async with AsyncSessionLocal() as s:
            from backend.database.repositories import AudioTrackRepository
            tracks = await AudioTrackRepository(s).list_for_project(pid)
        if not tracks:
            raise CommandError("Add a song first (＋🎵 in the media bin), then I can cut to its beat.")
        rows, plan, analysis = await editing.plan_project_beat_sync(pid, tracks[0].id, a.get("every"))
        await editing.apply_rows(pid, plan.rows, "Before beat sync (auto-saved)")
        return (f"Synced {plan.synced} cut(s) to the beat of “{tracks[0].original_filename}” "
                f"({analysis['bpm']:.0f} BPM, every {plan.every} beat{'s' if plan.every > 1 else ''})"
                + (f"; {plan.unsynced} clip(s) were too short to reach a beat." if plan.unsynced else ".")), True

    if kind == "reframe":
        mode = a.get("mode")
        if mode not in ("smart", "center", "fit"):
            raise CommandError("Framing can be smart, center or fit.")
        await editing.update_settings(pid, {"reframe": {"mode": mode}})
        return {"smart": "Vertical/square outputs will now follow the subject (smart reframing).",
                "center": "Outputs with a different shape will crop to the centre.",
                "fit": "Outputs with a different shape will show the whole frame with bars."}[mode], True

    if kind == "apply_style":
        from backend.database.models import Project, StyleStatus
        name = str(a.get("name", "")).strip().lower()
        async with AsyncSessionLocal() as s:
            project = await s.get(Project, pid)
            profiles = await StyleRepository(s).list_profiles(project.user_id)
        ready = [p for p in profiles if p.status == StyleStatus.READY]
        match = next((p for p in ready if p.name.lower() == name), None) or \
            next((p for p in ready if name in p.name.lower() or p.name.lower() in name), None)
        if not match:
            names = ", ".join(p.name for p in ready) or "none trained yet"
            raise CommandError(f"I couldn't find a trained style called “{a.get('name')}”. Your styles: {names}.")
        from backend.style.service import plan_timeline_with_style
        plan = await plan_timeline_with_style(pid, match.id, project.target_duration_sec)
        await editing.apply_rows(pid, plan["rows"], f"Before style '{match.name}' (auto-saved)")
        return f"Re-cut in your “{match.name}” style: {plan['summary']}.", True

    if kind == "fit_duration":
        return await _fit_duration(pid, float(a["seconds"]))

    if kind == "render":
        from backend.core.local_pipeline import rerender
        from backend.database.models import Project
        async with AsyncSessionLocal() as s:
            project = await s.get(Project, pid)
        r = await rerender(str(pid), project.output_formats)
        return (f"Rendered {len(project.output_formats or [])} format(s), {r['total_duration_ms'] / 1000:.1f}s"
                + (f" — notes: {'; '.join(r['warnings'])}" if r.get("warnings") else "") + "."), False

    raise CommandError(f"I don't know how to “{kind}”. {HELP}")


async def _fit_duration(pid: uuid.UUID, seconds: float) -> Tuple[str, bool]:
    """Shorten the edit to ~`seconds`: drop the weakest clips (never the opener), then trim evenly."""
    if seconds < 3:
        raise CommandError("That's too short — pick at least 3 seconds.")
    target = seconds * 1000
    async with AsyncSessionLocal() as s:
        entries = await StoryTimelineRepository(s).get_ordered(pid)
    rows = rows_from_entries(entries)
    for r, e in zip(rows, [e for e in entries if e.segment]):
        r.update({"segment_id": str(e.segment_id), "narrative_role": e.narrative_role.value,
                  "edit_reasoning": e.edit_reasoning, "reframe_params": e.reframe_params,
                  "score": (e.segment.engagement_score or 0) + (e.segment.quality_score or 0)})
    current = total_ms(place(rows))
    if current <= target + 250:
        return f"The edit is already {current / 1000:.1f}s — within {seconds:g}s.", False
    while len(rows) > 1 and total_ms(place(rows)) > target * 1.25:
        weakest = min(rows[1:], key=lambda r: r["score"])
        rows.remove(weakest)
    over = total_ms(place(rows)) - target
    if over > 0:
        spans = [r["src_out"] - r["src_in"] for r in rows]
        trimmable = sum(max(0, sp - 800) for sp in spans)
        if trimmable > 0:
            for r, sp in zip(rows, spans):
                cut = over * max(0, sp - 800) / trimmable * normalize_effects(r.get("effects"))["speed"]
                r["src_in"] += int(cut / 2)
                r["src_out"] -= int(cut / 2)
    new_rows = [{"segment_id": r["segment_id"], "narrative_role": r["narrative_role"], "transition_in": r["transition_in"],
                 "trim_start_ms": r["src_in"], "trim_end_ms": r["src_out"], "effects": r.get("effects"),
                 "reframe_params": r.get("reframe_params"), "edit_reasoning": r.get("edit_reasoning")} for r in rows]
    from backend.editing import service as editing
    await editing.apply_rows(pid, new_rows, f"Before fitting to {seconds:g}s (auto-saved)")
    return (f"Cut the edit from {current / 1000:.1f}s to {total_ms(place(rows)) / 1000:.1f}s "
            f"({len(entries) - len(rows)} weaker clip(s) removed, the rest tightened)."), True


# ── Claude interpreter (optional) ─────────────────────────────────────────────

ACTION_TYPES = ["remove", "move", "trim", "split", "duplicate", "transition", "speed", "mute", "volume", "filter",
                "adjust", "add_text", "captions", "cleanup", "beat_sync", "reframe", "apply_style", "fit_duration",
                "render", "help"]

EDIT_TOOL = {
    "name": "edit_timeline",
    "description": (
        "Apply one or more edits to the user's video timeline, in order. Clips are addressed by their "
        "1-based timeline position, or \"all\". Use -1 for the last clip. Times are seconds."),
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ACTION_TYPES},
                        "clips": {"description": "Target clip positions or the string \"all\"",
                                  "anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "string", "enum": ["all"]}]},
                        "clip": {"type": "integer", "description": "Single clip position (move/trim/split/duplicate)"},
                        "to": {"type": "integer", "description": "move: destination position"},
                        "edge": {"type": "string", "enum": ["start", "end"]},
                        "seconds": {"type": "number", "description": "trim: seconds into the clip; fit_duration: target length"},
                        "at_seconds": {"type": "number"},
                        "duration_seconds": {"type": "number"},
                        "value": {"description": "transition name (cut, dissolve, cross_fade, fade_to_black, wipe, zoom_in, zoom_out) or speed factor 0.25-4",
                                  "anyOf": [{"type": "string"}, {"type": "number"}]},
                        "percent": {"type": "number"},
                        "relative": {"type": "number"},
                        "muted": {"type": "boolean"},
                        "name": {"type": "string", "description": "filter (none, bw, warm, cool, vivid, vintage, cinematic) or style name"},
                        "property": {"type": "string", "enum": ["brightness", "contrast", "saturation"]},
                        "delta": {"type": "number", "description": "+1 increase, -1 decrease"},
                        "text": {"type": "string"},
                        "position": {"type": "string", "enum": ["top", "center", "bottom"]},
                        "enabled": {"type": "boolean"},
                        "generate": {"type": "boolean"},
                        "preset": {"type": "string", "enum": ["classic", "bold", "karaoke", "minimal"]},
                        "uppercase": {"type": "boolean"},
                        "silence": {"type": "boolean"},
                        "fillers": {"type": "boolean"},
                        "every": {"type": "integer", "description": "beat_sync: cut every N beats (1, 2, 4)"},
                        "mode": {"type": "string", "enum": ["smart", "center", "fit"]},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["actions"],
    },
}


async def _timeline_context(pid: uuid.UUID) -> str:
    from backend.editing import service as editing
    async with AsyncSessionLocal() as s:
        entries = await StoryTimelineRepository(s).get_ordered(pid)
    rows = rows_from_entries(entries)
    placed = place(rows)
    lines = [f"Timeline: {len(placed)} clips, {total_ms(placed) / 1000:.1f}s total."]
    for p, e in zip(placed, [e for e in entries if e.segment]):
        fx = normalize_effects(e.effects)
        extra = ", ".join(x for x in (f"{fx['speed']:g}x" if fx["speed"] != 1 else "", "muted" if fx["muted"] else "",
                                       fx["filter"] if fx["filter"] != "none" else "") if x)
        lines.append(f"  clip {p.index + 1}: {p.duration / 1000:.1f}s at {p.start / 1000:.1f}s, role "
                     f"{e.narrative_role.value}, transition in {e.transition_in.value}{', ' + extra if extra else ''}")
    st = await editing.get_settings(pid)
    lines.append(f"Captions: {'on' if st['captions']['enabled'] else 'off'} ({st['captions']['preset']}); "
                 f"framing: {st['reframe']['mode']}.")
    return "\n".join(lines)


class EditingInterpreter(CommandInterpreterProvider):
    """Claude tool-use when ANTHROPIC_API_KEY is set (any phrasing); the rule grammar otherwise or on failure."""

    async def interpret(self, message: str, project_id: str) -> InterpretResult:
        actions, provider = None, "rules"
        if settings.ANTHROPIC_API_KEY:
            try:
                actions, text = await self._claude(message, uuid.UUID(project_id))
                provider = "claude"
                if not actions:
                    return InterpretResult(reply=text or HELP, provider=provider)
            except Exception as e:  # noqa: BLE001 — fall back to the deterministic grammar
                logger.warning("Claude interpreter failed, using rules: %s", e)
                actions = None
        if actions is None:
            actions = parse_rules(message)
        if not actions:
            return InterpretResult(reply=f"I didn't understand that. {HELP}", provider=provider)
        reply, action = await execute(project_id, actions)
        return InterpretResult(reply=reply, action=action, provider=provider)

    async def _claude(self, message: str, pid: uuid.UUID) -> Tuple[Optional[List[dict]], str]:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        system = ("You are the editing assistant inside a video editor. Turn the user's request into calls to the "
                  "edit_timeline tool. If the request is ambiguous or impossible, reply briefly in text instead "
                  "of calling the tool. Current project state:\n" + await _timeline_context(pid))
        resp = await client.messages.create(
            model=settings.CLAUDE_EDIT_MODEL, max_tokens=2048, system=system,
            tools=[EDIT_TOOL], tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": message}],
        )
        if resp.stop_reason == "refusal":
            return None, "I can't help with that request."
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        for b in resp.content:
            if b.type == "tool_use" and b.name == "edit_timeline":
                acts = b.input.get("actions") if isinstance(b.input, dict) else None
                if isinstance(acts, list) and all(isinstance(x, dict) and x.get("type") in ACTION_TYPES for x in acts):
                    return acts, text
        return [], text
