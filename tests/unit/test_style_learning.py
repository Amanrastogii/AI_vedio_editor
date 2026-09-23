import numpy as np

from backend.style import align, learner, media, planner
from backend.style.features import FEATURE_NAMES, windows_for_shot
from backend.style.profile import aggregate_style, style_effects, summarize, train


def _rng():
    return np.random.default_rng(42)


# ── learner ───────────────────────────────────────────────────────────────────

def test_selector_learns_a_separable_rule():
    rng = _rng()
    X = rng.normal(size=(200, 4))
    y = (X[:, 1] > 0.2).astype(float)
    sc = learner.Standardizer.fit(X)
    m = learner.train_selector(sc.transform(X), y)
    acc = ((learner.predict_selector(m, sc.transform(X)) >= 0.5) == y).mean()
    assert acc > 0.9
    assert np.argmax(np.abs(m["w"])) == 1


def test_selector_single_class_is_constant():
    X = np.ones((5, 3))
    m = learner.train_selector(X, np.ones(5))
    assert m["constant"] == 1.0
    assert np.allclose(learner.predict_selector(m, X), 1.0)


def test_memory_retrieves_similar_decisions_and_trims():
    Z = np.array([[1, 0], [0.9, 0.1], [-1, 0], [-0.9, -0.1]], dtype=float)
    kept = np.array([1, 1, 0, 0])
    dec = [{"head_frac": 0.2, "keep_frac": 0.5}, {"head_frac": 0.4, "keep_frac": 0.7}, {}, {}]
    mem = learner.Memory(Z, kept, dec, ["a", "b", "c", "d"])
    p = mem.keep_probability(np.array([[1.0, 0.05], [-1.0, 0.0]]), k=2)
    assert p[0] > 0.9 and p[1] < 0.1
    advice = mem.trim_advice(np.array([1.0, 0.0]), k=2)
    assert 0.2 <= advice["head_frac"] <= 0.4 and 0.5 <= advice["keep_frac"] <= 0.7
    assert mem.explain(np.array([1.0, 0.0]))[0]["kept"] is True


def test_evaluate_reports_low_data_honestly():
    rng = _rng()
    X = rng.normal(size=(10, 3))
    y = np.array([1, 0] * 5, dtype=float)
    out = learner.evaluate(X, y, np.array(["a"] * 10))
    assert out["low_data"] is True
    assert out["cv"]["scheme"] == "5-fold"


# ── alignment ─────────────────────────────────────────────────────────────────

def _textured_frames(n, seed):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, size=(n, 10, 10, 3)).astype(np.uint8)
    return np.repeat(np.repeat(base, 8, axis=1), 8, axis=2)  # 80×80 blocky frames


def test_phash_matches_regraded_frames_and_rejects_others():
    f = _textured_frames(3, 1)
    box = (0, 80, 0, 80)
    h, inf = media.phash(f, box)
    graded = np.clip(f.astype(int) * 1.15 + 12, 0, 255).astype(np.uint8)
    hg, _ = media.phash(graded, box)
    d = media.hamming_matrix(h, hg)
    assert inf.all()
    assert all(d[i, i] <= 6 for i in range(3))
    assert d[0, 1] > align.MATCH_THRESH


def test_content_box_strips_letterbox():
    f = np.zeros((4, 80, 80, 3), dtype=np.uint8)
    f[:, 20:60, :, :] = 200
    y0, y1, x0, x1 = media.content_box(f)
    assert (y0, y1, x0, x1) == (20, 60, 0, 80)
    # a caption drawn in the bottom bar for a minority of frames must not widen the box
    f[:1, 65:75, 20:60, :] = 255
    assert media.content_box(f)[:2] == (20, 60)


def test_crop_box_for_vertical_edit():
    y0, y1, x0, x1 = media.crop_box_for_aspect(16 / 9, 9 / 16)
    assert y0 == 0 and y1 == 80 and (x1 - x0) < 30


def test_map_shots_recovers_source_range_and_speed():
    # edit 0-2000ms shows raw clip 1 from 5000ms at 2x speed
    matches = [align.FrameMatch(t_edit=t, clip=1, t_raw=5000 + 2 * t, raw_idx=0, dist=3)
               for t in range(0, 2000, 200)]
    shots = align.map_shots([(0, 2000)], matches, [60_000, 60_000])
    s = shots[0]
    assert s.matched and s.clip == 1
    assert abs(s.speed - 2.0) < 0.01
    assert abs(s.src_in - 5000) < 50 and abs(s.src_out - 9000) < 50


def test_map_shots_splits_on_missed_cut():
    m = [align.FrameMatch(t_edit=t, clip=0, t_raw=1000 + t, raw_idx=0, dist=2) for t in range(0, 1000, 200)]
    m += [align.FrameMatch(t_edit=t, clip=0, t_raw=30_000 + t, raw_idx=0, dist=2) for t in range(1000, 2000, 200)]
    shots = align.map_shots([(0, 2000)], m, [60_000])
    assert len(shots) == 2
    assert abs(shots[1].src_in - 31_000) < 300


# ── profile + planner ─────────────────────────────────────────────────────────

def _fake_analysis(seed):
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(30):
        f = rng.normal(size=len(FEATURE_NAMES)).tolist()
        kept = f[6] > 0  # "motion" drives the decision
        samples.append({"features": f, "kept": kept, "description": f"w{i}", "source": "raw.mp4",
                        "decision": {"head_frac": 0.1, "keep_frac": 0.6} if kept else {}})
    return {
        "edited": {"duration_ms": 30_000, "width": 1080, "height": 1920},
        "duration_ratio": 0.25, "coverage": 0.9,
        "pacing": {"median_shot_ms": 2000, "p25_shot_ms": 1500, "p75_shot_ms": 3000, "cuts_per_min": 28},
        "transitions": {"cut": 8, "dissolve": 2}, "speed": {"median": 1.0, "changed_fraction": 0.0},
        "color": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.25, "warmth": 0.04, "samples": 40},
        "audio": {"mode": "replaced", "correlation": 0.1, "loudness_db": -20},
        "order": {"kendall_tau": 0.8, "hook_from_later": True},
        "samples": samples, "kept_windows": sum(s["kept"] for s in samples), "total_windows": 30,
    }


def test_train_aggregate_and_summarize():
    analyses = [_fake_analysis(1), _fake_analysis(2)]
    style = aggregate_style(analyses)
    assert style["aspect"] == "9:16"
    assert style["audio"]["mode"] == "replaced"
    assert abs(style["transitions"]["dissolve"] - 0.2) < 1e-6
    model, metrics, mem = train(analyses, ["e1", "e2"])
    assert metrics["cv"]["scheme"] == "leave-one-example-out"
    assert metrics["cv"]["balanced_accuracy"] > 0.75
    assert metrics["importance"][0]["feature"] == "motion"
    assert len(mem) == 60
    fx = style_effects(style)
    assert fx["saturation"] > 1 and fx["filter"] == "warm" and fx["volume"] == 0.15
    text = summarize("Test", style, metrics)
    assert "cut about every 2.0s" in text and "lots of motion" in text


def test_planner_prefers_learned_shots_and_hits_target():
    analyses = [_fake_analysis(1), _fake_analysis(2)]
    style = aggregate_style(analyses)
    model, metrics, mem_rows = train(analyses, ["e1", "e2"])
    sc = learner.Standardizer.from_dict(model["scaler"])
    memory = learner.Memory(sc.transform(np.array([m["features"] for m in mem_rows])),
                            np.array([m["kept"] for m in mem_rows], dtype=float),
                            [m["decision"] for m in mem_rows], [m["description"] for m in mem_rows])
    rng = np.random.default_rng(7)
    windows = []
    for i in range(20):
        f = rng.normal(size=len(FEATURE_NAMES))
        f[6] = 2.0 if i % 2 == 0 else -2.0       # even windows are "high motion"
        windows.append(planner.Window(segment_id=f"s{i}", clip_order=1, start_ms=i * 4000,
                                      end_ms=i * 4000 + 4000, seg_start=i * 4000, seg_end=i * 4000 + 4000,
                                      features=f.tolist()))
    plan = planner.plan_timeline(windows, model, memory, style, "Test", raw_total_ms=80_000)
    picked = {r["segment_id"] for r in plan.rows}
    assert picked and all(int(s[1:]) % 2 == 0 for s in picked)
    assert plan.rows[0]["transition_in"] == "cut"
    assert all(r["trim_end_ms"] > r["trim_start_ms"] for r in plan.rows)
    assert plan.planned_ms <= plan.target_ms + 4000
    assert plan.rows[0]["effects"]["volume"] == 0.15


def test_percentile_rank_ties_share_rank():
    from backend.style.features import _pct_rank
    assert np.allclose(_pct_rank(np.array([-90.0, -90.0, -90.0])), 0.5)
    assert np.allclose(_pct_rank(np.array([1.0, 3.0, 2.0])), [0.0, 1.0, 0.5])


def test_windows_split_long_shots():
    assert windows_for_shot(0, 5000) == [(0, 5000)]
    w = windows_for_shot(0, 20_000)
    assert len(w) == 5 and w[-1][1] == 20_000
