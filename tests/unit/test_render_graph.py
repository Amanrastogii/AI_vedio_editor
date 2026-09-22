from pathlib import Path

import numpy as np

from backend.core import render_graph as rg


def test_normalize_effects_clamps_and_defaults():
    e = rg.normalize_effects({"speed": 10, "volume": -1, "filter": "nope", "unknown": 1})
    assert e["speed"] == 4.0 and e["volume"] == 0.0 and e["filter"] == "none"
    assert "unknown" not in e
    assert rg.normalize_effects(None) == rg.DEFAULT_EFFECTS


def _tempos(chain):
    return [float(c.split("=")[1]) for c in chain]


def test_atempo_chain_stays_in_range():
    assert rg.atempo_chain(1.0) == []
    for speed in (4.0, 0.25, 3.0, 0.3, 1.5):
        vals = _tempos(rg.atempo_chain(speed))
        assert all(0.5 <= v <= 2.0 for v in vals)
        assert abs(float(np.prod(vals)) - speed) < 1e-3


def test_part_duration_accounts_for_speed():
    assert rg.part_output_duration(0, 4000, {"speed": 2}) == 2.0
    assert rg.part_output_duration(1000, 1100, None) == 0.3  # minimum source length


def test_part_filter_has_exact_length_audio_and_effects():
    fc = rg.build_part_filter(1080, 1920, {"speed": 2, "saturation": 1.3, "muted": True, "filter": "bw"},
                              2.0, "0:a")
    assert "setpts=PTS/2.0000" in fc and "eq=" in fc and "hue=s=0" in fc
    assert "[0:a]atempo=2.0000" in fc
    assert "volume=0.000" in fc
    assert "apad,atrim=duration=2.000" in fc
    # silent source: no atempo on the anullsrc input
    assert "atempo" not in rg.build_part_filter(1920, 1080, {"speed": 2}, 1.0, "1:a").split(";")[1]


def test_join_filter_mixes_xfade_and_concat():
    # transitions[i] is the transition INTO part i: hard cut 0→1, dissolve 1→2
    plan = rg.build_join_filter([3.0, 3.0, 3.0], ["cut", "cut", "dissolve"])
    fc = plan.filter_complex
    assert "xfade=transition=dissolve:duration=0.500:offset=5.500" in fc
    assert "acrossfade=d=0.500" in fc
    assert "concat=n=2:v=1:a=1" in fc
    assert abs(plan.total_duration - 8.5) < 1e-6
    assert fc.endswith("[aout]")


def test_join_normalizes_timebase_between_concat_and_xfade():
    # regression: xfade after a concat failed with "Invalid argument" (timebase mismatch)
    fc = rg.build_join_filter([3, 3, 3, 3], ["cut", "dissolve", "cut", "fade_to_black"]).filter_complex
    assert "[c2]settb=AVTB[v2]" in fc
    assert "[v2][in3]xfade=transition=fadeblack" in fc
    assert all(f"[{i}:v]settb=AVTB[in{i}]" in fc for i in range(4))


def test_join_single_part():
    plan = rg.build_join_filter([2.0], ["cut"])
    assert "[in0]null[vout]" in plan.filter_complex and plan.total_duration == 2.0
    assert not rg.needs_xfade(["cut"])
    assert rg.needs_xfade(["cut", "wipe"])


def test_short_parts_get_short_transitions():
    plan = rg.build_join_filter([0.4, 0.4], ["cut", "fade_to_black"])
    assert "duration=0.200" in plan.filter_complex


def test_finish_filter_music_ducking_and_skipped_inputs():
    music = [
        rg.MusicTrack(path=Path("late.mp3"), start_ms=50_000),              # starts after the video ends
        rg.MusicTrack(path=Path("song.mp3"), start_ms=2000, duck_original=0.3, loop=True),
    ]
    plan = rg.build_finish_filter(10.0, music, [], 1920, 1080, None, True, None)
    # the skipped track must not consume an input index
    assert plan.input_args == ["-stream_loop", "-1", "-i", "song.mp3"]
    assert "[1:a]atrim=start=0.000:duration=8.000" in plan.filter_complex
    assert "adelay=2000|2000" in plan.filter_complex
    assert "volume=enable='between(t,2.000,10.000)':volume=0.300" in plan.filter_complex
    assert "amix=inputs=2" in plan.filter_complex and "loudnorm" in plan.filter_complex
    assert plan.video_map == "0:v" and not plan.reencode_video


def test_finish_filter_overlays_reencode(tmp_path):
    tf = tmp_path / "t.txt"
    tf.write_text("Hello: world")
    ov = rg.Overlay(textfile=tf, start_ms=0, end_ms=2000, position="top")
    plan = rg.build_finish_filter(5.0, [], [ov], 1080, 1920, None, False, None)
    assert plan.reencode_video and plan.video_map == "[vout]"
    assert "drawtext=textfile=" in plan.filter_complex and "enable='between(t,0.000,2.000)'" in plan.filter_complex
    assert "[orig]anull[aout]" in plan.filter_complex


def test_path_escape_for_windows_drive():
    assert rg.ffmpeg_path_escape(Path("C:/Windows/Fonts/arial.ttf")).startswith("C\\:/")
