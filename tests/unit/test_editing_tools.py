import numpy as np
import pytest

from backend.audio.beats import beat_grid
from backend.core import render_graph as rg
from backend.editing import captions as cap
from backend.editing import settings as es
from backend.editing.beatsync import boundary_errors, plan_beat_sync
from backend.editing.cleanup import CleanupOptions, keep_ranges, plan_cleanup
from backend.editing.commands import parse_rules, parse_targets
from backend.editing.layout import place, total_ms
from backend.editing.reframe import crop_filter, smooth_path


def row(clip, a, b, seg=(0, 60_000), tr="cut", speed=1.0, sid=None):
    return {"clip_id": clip, "segment_id": sid or f"s-{clip}-{a}", "src_in": a, "src_out": b,
            "seg_start": seg[0], "seg_end": seg[1], "transition_in": tr,
            "effects": {"speed": speed} if speed != 1 else None}


# ── layout mirrors the renderer ───────────────────────────────────────────────

def test_layout_matches_render_graph_join_math():
    rows = [row("a", 0, 3000), row("b", 0, 4000, tr="dissolve", speed=2), row("c", 1000, 3500, tr="cut")]
    placed = place(rows)
    durs = [rg.part_output_duration(r["src_in"], r["src_out"], r["effects"]) for r in rows]
    plan = rg.build_join_filter(durs, [r["transition_in"] for r in rows])
    assert abs(total_ms(placed) / 1000 - plan.total_duration) < 1e-6
    assert placed[1].duration == 2000 and placed[1].overlap_in == 500
    assert placed[1].start == 2500 and placed[2].start == placed[1].end


# ── captions ──────────────────────────────────────────────────────────────────

WORDS = {"a": [{"id": "1", "word": "Hello", "start_ms": 1000, "end_ms": 1400},
               {"id": "2", "word": "um,", "start_ms": 1500, "end_ms": 1700, "is_filler": True},
               {"id": "3", "word": "world.", "start_ms": 1800, "end_ms": 2200},
               {"id": "4", "word": "Outside", "start_ms": 5000, "end_ms": 5400}]}


def test_caption_words_follow_trim_and_speed():
    placed = place([row("x", 0, 1000), row("a", 1000, 3000, speed=2)])
    words = cap.sequence_words(placed, WORDS, hide_fillers=True)
    assert [w.text for w in words] == ["Hello", "world."]          # filler hidden, out-of-range word dropped
    assert words[0].start == 1000 and words[0].end == 1200         # (1000-1000)/2 + clip start 1000
    assert words[1].start == 1400


def test_cues_break_on_sentence_and_min_duration():
    placed = place([row("a", 0, 6000)])
    words = cap.sequence_words(placed, WORDS, hide_fillers=False)
    cues = cap.build_cues(words, max_chars=32)
    assert cues[0].text == "Hello um, world." and cues[1].text == "Outside"
    assert cues[1].end - cues[1].start >= cap.MIN_CUE_MS


def test_caption_writers():
    cues = cap.build_cues([cap.CueWord("Hi", 0, 400), cap.CueWord("there.", 450, 900)])
    assert cap.to_srt(cues).startswith("1\n00:00:00,000 --> 00:00:01,200\nHi there.")
    assert cap.to_vtt(cues).startswith("WEBVTT\n\n00:00:00.000 --> ")
    ass = cap.to_ass(cues, es.resolve({})["captions"], 1080, 1920)
    assert "PlayResY: 1920" in ass and ass.count("Dialogue:") == 2          # bold preset: one event per word
    assert "{\\c&H0000D4FF}Hi{\\c&H00FFFFFF}" in ass                      # active word highlighted
    k = cap.to_ass(cues, {**es.resolve({})["captions"], "preset": "karaoke"}, 1920, 1080)
    assert "\\kf" in k and k.count("Dialogue:") == 1


def test_ass_vertical_captions_fit_the_frame():
    # regression: font was scaled by height, so 9:16 captions were ~2x too big and ran off the edges
    words = [cap.CueWord(w, i * 300, i * 300 + 250) for i, w in enumerate(
        "Welcome back to the channel today we are going hiking in the mountains".split())]
    cues = cap.build_cues(words, max_chars=32, max_lines=3)
    cfg = {**es.resolve({})["captions"], "preset": "classic"}
    ass = cap.to_ass(cues, cfg, 1080, 1920)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Cap,"))
    assert style.split(",")[2] == "64"
    fit = int((1080 - 2 * int(1080 * 0.06)) / (0.55 * 64))
    for d in (line for line in ass.splitlines() if line.startswith("Dialogue:")):
        for ln in d.split(",", 9)[9].split("\\N"):          # Text is the 10th Dialogue field
            assert len(ln) <= fit, ln


# ── cleanup ───────────────────────────────────────────────────────────────────

def test_keep_ranges():
    assert keep_ranges(0, 10_000, [(2000, 3000), (2900, 4000)], 250) == [(0, 2000), (4000, 10_000)]
    assert keep_ranges(0, 1000, [(100, 900)], 250) == []


def test_cleanup_removes_silence_and_fillers_only_in_speech_clips():
    words = {"talk": [{"word": w, "start_ms": s, "end_ms": s + 300, "is_filler": w == "um"}
                      for w, s in (("hi", 0), ("there", 400), ("um", 800), ("friends", 5000), ("bye", 5400))],
             "broll": []}
    sil = {"talk": [(1200, 4900)], "broll": [(0, 8000)]}
    rows = [row("talk", 0, 6000), row("broll", 0, 8000)]
    plan = plan_cleanup(rows, sil, words, CleanupOptions())
    talk = [r for r in plan.rows if r["segment_id"].startswith("s-talk")]
    assert [(r["trim_start_ms"], r["trim_end_ms"]) for r in talk] == [(0, 760), (4750, 6000)]
    assert talk[1]["transition_in"] == "cut"
    assert any(r["segment_id"].startswith("s-broll") and r["trim_end_ms"] == 8000 for r in plan.rows)  # untouched
    assert plan.clips_skipped_no_speech == 1 and plan.fillers_removed == 1 and plan.silences_removed == 1


# ── beats ─────────────────────────────────────────────────────────────────────

def test_beat_grid_offset_loop_and_stride():
    a = {"beats_ms": [0, 500, 1000, 1500], "duration_ms": 2000, "downbeat_phase": 0}
    assert beat_grid(a, 1000, 500, None, False, 10_000) == [1000, 1500, 2000]
    looped = beat_grid(a, 0, 0, None, True, 5000)
    assert looped[:6] == [0, 500, 1000, 1500, 2000, 2500]
    assert beat_grid(a, 0, 0, None, True, 5000, every=2)[:3] == [0, 1000, 2000]


def test_beat_sync_puts_cuts_on_beats():
    beats = list(range(0, 60_000, 500))       # 120 BPM
    rows = [row("a", 0, 1800), row("b", 0, 2300), row("c", 0, 900), row("d", 0, 3000)]
    plan = plan_beat_sync(rows, beats, every=1)
    assert plan.synced == 3
    assert max(boundary_errors([{**r, "src_in": p["trim_start_ms"], "src_out": p["trim_end_ms"]}
                                for r, p in zip(rows, plan.rows)], beats)) < 5
    assert plan.mean_error_ms is not None and plan.mean_error_ms < 5


def test_beat_sync_with_crossfade_aligns_midpoint():
    beats = list(range(0, 60_000, 600))
    rows = [row("a", 0, 2000), row("b", 0, 2500, tr="dissolve"), row("c", 0, 2000)]
    plan = plan_beat_sync(rows, beats)
    fixed = [{**r, "src_in": p["trim_start_ms"], "src_out": p["trim_end_ms"]} for r, p in zip(rows, plan.rows)]
    assert max(boundary_errors(fixed, beats)) < 30


def test_beat_sync_respects_segment_bounds():
    beats = [0, 5000, 10_000]
    rows = [row("a", 0, 1000, seg=(0, 1200)), row("b", 0, 1000)]
    plan = plan_beat_sync(rows, beats)
    assert plan.unsynced == 1 and plan.rows[0]["trim_end_ms"] == 1000


# ── reframe ───────────────────────────────────────────────────────────────────

def test_crop_filter_modes():
    assert crop_filter(1920, 1080, 1920, 1080, "smart") is None              # same aspect
    assert crop_filter(1920, 1080, 1080, 1920, "fit") is None                # letterbox
    c = crop_filter(1920, 1080, 1080, 1920, "center")
    assert c == "crop=606:1080:'657.0':0"
    track = {"points": [[0, 0.2, 0.5], [1000, 0.2, 0.5], [2000, 0.8, 0.5]]}
    s = crop_filter(1920, 1080, 1080, 1920, "smart", track, 0, 2000)
    assert s.startswith("crop=606:1080:'if(lt(t,") and "\\," not in s     # quoted, commas unescaped
    m = crop_filter(1920, 1080, 1080, 1920, "center", None, 0, 1000, manual_x=0.0)
    assert m == "crop=606:1080:'0.0':0"                                       # clamped to the left edge
    v = crop_filter(1080, 1920, 1920, 1080, "center")                        # vertical → horizontal: crop height
    assert v.startswith("crop=1080:606:0:")


def test_smooth_path_zero_lag_and_deadzone():
    ramp = np.linspace(0.1, 0.9, 40)
    out = smooth_path(ramp)
    assert abs(out[20] - ramp[20]) < 0.05                                    # no lag in the middle
    jitter = 0.5 + 0.02 * np.sin(np.arange(40))
    assert np.ptp(smooth_path(jitter)) < 0.01                                # small drift doesn't pan


# ── commands ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("remove clip 2", {"type": "remove", "clips": [2]}),
    ("delete clips 2 and 4", {"type": "remove", "clips": [2, 4]}),
    ("move clip 3 to the start", {"type": "move", "clip": 3, "to": 1}),
    ("split clip 2 at 3.5s", {"type": "split", "clip": 2, "at_seconds": 3.5}),
    ("trim clip 2 start to 5s", {"type": "trim", "clip": 2, "edge": "start", "seconds": 5.0}),
    ("duplicate the first clip", {"type": "duplicate", "clip": 1}),
    ("speed up clip 2 to 2x", {"type": "speed", "clips": [2], "value": 2.0}),
    ("slow down clip 3", {"type": "speed", "clips": [3], "value": 0.5}),
    ("mute all clips", {"type": "mute", "clips": "all", "muted": True}),
    ("set the volume of clip 2 to 50%", {"type": "volume", "clips": [2], "percent": 50.0}),
    ("make clip 1 black and white", {"type": "filter", "clips": [1], "name": "bw"}),
    ("brighten clips 1-3", {"type": "adjust", "clips": [1, 2, 3], "property": "brightness", "delta": 1}),
    ("use dissolve transitions everywhere", {"type": "transition", "clips": "all", "value": "dissolve"}),
    ("remove silences and filler words", {"type": "cleanup", "silence": True, "fillers": True}),
    ("remove the ums", {"type": "cleanup", "silence": False, "fillers": True}),
    ("sync the cuts to the beat every bar", {"type": "beat_sync", "every": 4}),
    ("turn off captions", {"type": "captions", "enabled": False}),
    ("make it 30 seconds", {"type": "fit_duration", "seconds": 30.0}),
    ("smart crop", {"type": "reframe", "mode": "smart"}),
    ("apply style Travel vlogs", {"type": "apply_style", "name": "travel vlogs"}),
    ("render", {"type": "render"}),
])
def test_rule_grammar(text, expected):
    actions = parse_rules(text)
    assert actions and actions[0] == expected


def test_rule_grammar_compound_and_text():
    a = parse_rules("add text \"Day 1: Paris\" at 2s for 4s at the top; add karaoke captions")
    assert a[0] == {"type": "add_text", "text": "Day 1: Paris", "at_seconds": 2.0, "duration_seconds": 4.0,
                    "position": "top"}
    assert a[1]["type"] == "captions" and a[1]["preset"] == "karaoke"
    assert parse_rules("what's the weather like") is None
    assert parse_targets("the last clip") == [-1]


def test_settings_resolve_validates():
    s = es.resolve({"captions": {"preset": "nope", "font_size": 9999, "highlight_color": "red"},
                    "reframe": {"mode": "zoom"}, "junk": {}})
    assert s["captions"]["preset"] == "bold" and s["captions"]["font_size"] == 160
    assert s["captions"]["highlight_color"] == "#FFD400" and s["reframe"]["mode"] == "smart"
