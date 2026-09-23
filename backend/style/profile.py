"""Aggregate per-example analyses into one learned style + train the selector/memory."""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.style import learner
from backend.style.features import FEATURE_LABELS, FEATURE_NAMES


def _wavg(vals: List[Optional[float]], weights: List[float]) -> Optional[float]:
    pairs = [(v, w) for v, w in zip(vals, weights) if v is not None and not math.isnan(v)]
    if not pairs:
        return None
    tw = sum(w for _, w in pairs) or 1.0
    return float(sum(v * w for v, w in pairs) / tw)


def aggregate_style(analyses: List[Dict]) -> Dict:
    w = [float(a["edited"]["duration_ms"] or 1) for a in analyses]
    trans: Dict[str, int] = {}
    for a in analyses:
        for k, v in (a.get("transitions") or {}).items():
            trans[k] = trans.get(k, 0) + v
    total_t = sum(trans.values())
    looks = [a["color"] for a in analyses if a.get("color")]
    look_w = [float(l.get("samples", 1)) for l in looks]
    audio_votes: Dict[str, float] = {}
    for a, wi in zip(analyses, w):
        mode = (a.get("audio") or {}).get("mode") or "unknown"
        if mode != "unknown":
            audio_votes[mode] = audio_votes.get(mode, 0) + wi
    vertical = sum(wi for a, wi in zip(analyses, w) if a["edited"]["height"] > a["edited"]["width"])
    hooks = [a["order"]["hook_from_later"] for a in analyses if a["order"].get("hook_from_later") is not None]
    kept = sum(a.get("kept_windows", 0) for a in analyses)
    total = sum(a.get("total_windows", 0) for a in analyses)

    return {
        "pacing": {
            "median_shot_ms": _wavg([a["pacing"]["median_shot_ms"] for a in analyses], w),
            "p25_shot_ms": _wavg([a["pacing"]["p25_shot_ms"] for a in analyses], w),
            "p75_shot_ms": _wavg([a["pacing"]["p75_shot_ms"] for a in analyses], w),
            "cuts_per_min": _wavg([a["pacing"]["cuts_per_min"] for a in analyses], w),
        },
        "duration": {
            "ratio": _wavg([a.get("duration_ratio") for a in analyses], w),
            "typical_ms": float(np.median([a["edited"]["duration_ms"] for a in analyses])),
        },
        "keep_rate": round(kept / total, 3) if total else None,
        "transitions": {k: round(v / total_t, 3) for k, v in trans.items()} if total_t else {"cut": 1.0},
        "speed": {
            "median": _wavg([a["speed"]["median"] for a in analyses], w) or 1.0,
            "changed_fraction": _wavg([a["speed"]["changed_fraction"] for a in analyses], w) or 0.0,
        },
        "color": {
            k: _wavg([l.get(k) for l in looks], look_w) for k in ("brightness", "contrast", "saturation", "warmth")
        } if looks else None,
        "audio": {
            "mode": max(audio_votes, key=audio_votes.get) if audio_votes else "unknown",
            "correlation": _wavg([(a.get("audio") or {}).get("correlation") for a in analyses], w),
            "loudness_db": _wavg([(a.get("audio") or {}).get("loudness_db") for a in analyses], w),
        },
        "order": {
            "chronological": _wavg([a["order"].get("kendall_tau") for a in analyses], w),
            "hook_from_later": (sum(hooks) / len(hooks) > 0.5) if hooks else False,
        },
        "aspect": "9:16" if vertical > sum(w) / 2 else "16:9",
        "alignment_coverage": _wavg([a.get("coverage") for a in analyses], w),
    }


def style_effects(style: Dict) -> Dict:
    """Learned look/speed/audio → per-clip render effects (normalize_effects-compatible)."""
    fx: Dict = {}
    c = style.get("color")
    if c:
        if c.get("brightness") is not None and abs(c["brightness"]) > 0.015:
            fx["brightness"] = round(float(np.clip(c["brightness"], -0.15, 0.15)), 3)
        if c.get("contrast") and abs(c["contrast"] - 1) > 0.04:
            fx["contrast"] = round(float(np.clip(c["contrast"], 0.8, 1.4)), 3)
        if c.get("saturation") and abs(c["saturation"] - 1) > 0.05:
            fx["saturation"] = round(float(np.clip(c["saturation"], 0.5, 1.8)), 3)
        if c.get("warmth") is not None and abs(c["warmth"]) > 0.02:
            fx["filter"] = "warm" if c["warmth"] > 0 else "cool"
    sp = style.get("speed") or {}
    if sp.get("changed_fraction", 0) > 0.5 and abs(sp.get("median", 1) - 1) > 0.05:
        fx["speed"] = round(float(np.clip(sp["median"], 0.5, 2.0)), 2)
    mode = (style.get("audio") or {}).get("mode")
    if mode == "replaced":
        fx["volume"] = 0.15
    elif mode == "mixed":
        fx["volume"] = 0.5
    return fx


def train(analyses: List[Dict], example_ids: List[str]) -> Tuple[Dict, Dict, List[Dict]]:
    """→ (model_json, metrics, memory_rows). Memory rows carry example_id for provenance."""
    X, y, groups, mem_rows = [], [], [], []
    for a, ex_id in zip(analyses, example_ids):
        for s in a.get("samples", []):
            X.append(s["features"])
            y.append(1.0 if s["kept"] else 0.0)
            groups.append(ex_id)
            mem_rows.append({"example_id": ex_id, "features": s["features"], "kept": s["kept"],
                             "decision": s["decision"], "description": f'{s["description"]} — {s["source"]}'})
    if not X:
        raise ValueError("No usable raw footage windows were found in the examples.")
    X, y, g = np.array(X, dtype=np.float64), np.array(y), np.array(groups)
    metrics = learner.evaluate(X, y, g)
    sc = learner.Standardizer.fit(X)
    model = learner.train_selector(sc.transform(X), y)
    metrics["importance"] = learner.feature_importance(model, FEATURE_NAMES)[:8]
    model_json = {"scaler": sc.to_dict(), "selector": model, "feature_names": FEATURE_NAMES}
    return model_json, metrics, mem_rows


def summarize(name: str, style: Dict, metrics: Dict) -> str:
    """Deterministic plain-English description of the learned style."""
    lines = []
    p = style["pacing"]
    if p.get("median_shot_ms"):
        sec = p["median_shot_ms"] / 1000
        tempo = "fast" if sec < 2.5 else "medium" if sec < 5 else "slow, lingering"
        lines.append(f"Pacing: {tempo} — a cut about every {sec:.1f}s"
                     + (f" ({p['cuts_per_min']:.0f} cuts/min)." if p.get("cuts_per_min") else "."))
    d = style["duration"]
    if d.get("ratio"):
        lines.append(f"Keeps ~{d['ratio'] * 100:.0f}% of the raw footage; finished edits run "
                     f"~{d['typical_ms'] / 1000:.0f}s.")
    t = style.get("transitions") or {}
    if t:
        parts = [f"{k.replace('_', ' ')} {v * 100:.0f}%" for k, v in sorted(t.items(), key=lambda kv: -kv[1])]
        lines.append("Transitions: " + ", ".join(parts) + ".")
    o = style["order"]
    if o.get("chronological") is not None:
        lines.append("Story order: " + ("follows the footage chronologically" if o["chronological"] > 0.5
                                        else "rearranges the footage freely" if o["chronological"] < 0.1
                                        else "mostly chronological with some rearranging")
                     + ("; opens with a hook taken from later in the footage." if o.get("hook_from_later") else "."))
    c = style.get("color")
    if c:
        bits = []
        if c.get("brightness") is not None and abs(c["brightness"]) > 0.015:
            bits.append("brighter" if c["brightness"] > 0 else "darker")
        if c.get("contrast") and abs(c["contrast"] - 1) > 0.04:
            bits.append(f"{(c['contrast'] - 1) * 100:+.0f}% contrast")
        if c.get("saturation") and abs(c["saturation"] - 1) > 0.05:
            bits.append(f"{(c['saturation'] - 1) * 100:+.0f}% saturation")
        if c.get("warmth") is not None and abs(c["warmth"]) > 0.02:
            bits.append("warmer" if c["warmth"] > 0 else "cooler")
        lines.append("Color grade: " + (", ".join(bits) if bits else "kept close to the original") + ".")
    mode = (style.get("audio") or {}).get("mode")
    audio_txt = {"original": "keeps the original sound", "mixed": "mixes original sound with music/voice",
                 "replaced": "replaces the original sound with music / voice-over", "silent": "exports silent"}
    if mode in audio_txt:
        lines.append(f"Audio: {audio_txt[mode]}.")
    imp = metrics.get("importance") or []
    likes = [FEATURE_LABELS.get(i["feature"], i["feature"]) for i in imp if i["weight"] > 0.15][:3]
    dislikes = [FEATURE_LABELS.get(i["feature"], i["feature"]) for i in imp if i["weight"] < -0.15][:3]
    if likes:
        lines.append("Tends to keep: " + ", ".join(likes) + ".")
    if dislikes:
        lines.append("Tends to cut: " + ", ".join(dislikes) + ".")
    cv = metrics.get("cv")
    conf = (f"Learned from {metrics['examples']} example(s) and {metrics['samples']} keep/cut decisions"
            + (f"; held-out accuracy {cv['balanced_accuracy'] * 100:.0f}% (baseline {cv['majority_baseline'] * 100:.0f}%)"
               if cv and cv.get("balanced_accuracy") is not None else "")
            + (f"; {style['alignment_coverage'] * 100:.0f}% of edited footage matched to raw clips"
               if style.get("alignment_coverage") is not None else "") + ".")
    if metrics.get("low_data"):
        conf += " Add more examples for a sharper model."
    lines.append(conf)
    return "\n".join(lines)
