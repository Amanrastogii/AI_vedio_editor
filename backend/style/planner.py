"""
New footage + learned style → timeline rows (pure, unit-testable).

1. score every window: P(keep) = model ⊕ nearest remembered decisions
2. trim each window the way the editor trimmed similar shots, shaped to
   their typical shot length
3. pick the best windows until the learned target duration is filled
4. order: chronological if the editor is, with an optional hook up front
5. merge contiguous picks, assign learned transitions / grade / speed / audio
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from backend.style import learner
from backend.style.profile import style_effects

MIN_ENTRY_MS = 600


@dataclass
class Window:
    segment_id: str
    clip_order: int
    start_ms: int
    end_ms: int
    seg_start: int
    seg_end: int
    features: List[float]
    p: float = 0.0
    p_model: float = 0.0
    p_memory: float = 0.0
    trim_in: int = 0
    trim_out: int = 0
    neighbor: Optional[dict] = None


@dataclass
class Plan:
    rows: List[dict] = field(default_factory=list)
    target_ms: int = 0
    planned_ms: int = 0
    candidates: int = 0


def target_duration(style: Dict, raw_total_ms: int, requested_sec: Optional[int]) -> int:
    if requested_sec:
        return min(raw_total_ms, requested_sec * 1000)
    ratio = (style.get("duration") or {}).get("ratio") or 0.3
    typical = (style.get("duration") or {}).get("typical_ms") or 60_000
    t = raw_total_ms * ratio
    t = min(t, typical * 1.5)          # a much longer shoot shouldn't give a far longer edit
    return int(max(min(raw_total_ms, 5_000), min(raw_total_ms, t)))


def _transition_sequence(dist: Dict[str, float], n: int) -> List[str]:
    """Deterministic spread of learned transition proportions over n boundaries."""
    seq = ["cut"] * n
    others = sorted(((k, v) for k, v in dist.items() if k != "cut" and v > 0.05), key=lambda kv: -kv[1])
    pos = 0
    for name, frac in others:
        count = int(round(frac * n))
        if count <= 0:
            continue
        step = n / count
        for j in range(count):
            idx = min(n - 1, int(pos + j * step))
            seq[idx] = name
        pos += 1
    return seq


def plan_timeline(windows: List[Window], model_json: Dict, memory: learner.Memory, style: Dict,
                  profile_name: str, raw_total_ms: int, requested_sec: Optional[int] = None) -> Plan:
    plan = Plan(candidates=len(windows))
    if not windows:
        return plan
    sc = learner.Standardizer.from_dict(model_json["scaler"])
    Z = sc.transform(np.array([w.features for w in windows], dtype=np.float64))
    p_model = learner.predict_selector(model_json["selector"], Z)
    p_mem = memory.keep_probability(Z) if len(memory) else p_model
    p = learner.blend(p_model, p_mem, len(memory)) if len(memory) else p_model

    pace = (style.get("pacing") or {})
    median_shot = pace.get("median_shot_ms") or 3000
    lo, hi = max(MIN_ENTRY_MS, 0.5 * median_shot), max(MIN_ENTRY_MS * 2, 2.0 * median_shot)
    fx = style_effects(style)
    speed = float(fx.get("speed", 1.0))

    for i, w in enumerate(windows):
        w.p, w.p_model, w.p_memory = float(p[i]), float(p_model[i]), float(p_mem[i])
        advice = memory.trim_advice(Z[i]) if len(memory) else None
        dur = w.end_ms - w.start_ms
        head = (advice or {}).get("head_frac", 0.0)
        keep = (advice or {}).get("keep_frac", 1.0)
        length = float(np.clip(keep * dur, lo, hi))
        length = min(length, dur)
        start = w.start_ms + head * dur
        start = min(start, w.end_ms - length)
        w.trim_in, w.trim_out = int(max(w.start_ms, start)), int(max(w.start_ms, start) + length)
        exp = memory.explain(Z[i]) if len(memory) else []
        w.neighbor = exp[0] if exp else None

    # ── select ──
    plan.target_ms = target_duration(style, raw_total_ms, requested_sec)
    ranked = sorted(windows, key=lambda w: -w.p)
    chosen, total = [], 0.0
    for w in ranked:
        if total >= plan.target_ms:
            break
        if w.p < 0.2 and chosen and total >= 0.6 * plan.target_ms:
            break
        chosen.append(w)
        total += (w.trim_out - w.trim_in) / speed
    if not chosen:
        chosen = ranked[:1]

    # ── order ──
    order = style.get("order") or {}
    chrono = order.get("chronological")
    if chrono is None or chrono >= 0.3:
        chosen.sort(key=lambda w: (w.clip_order, w.trim_in))
    else:
        chosen.sort(key=lambda w: -w.p)
    if order.get("hook_from_later") and len(chosen) > 2:
        best = max(chosen, key=lambda w: w.p)
        chosen.remove(best)
        chosen.insert(0, best)

    # ── merge contiguous picks from the same segment ──
    merged: List[List[Window]] = []
    for w in chosen:
        last = merged[-1][-1] if merged else None
        if (last and last.segment_id == w.segment_id and 0 <= w.trim_in - last.trim_out <= 150
                and (w.trim_out - merged[-1][0].trim_in) <= 2.5 * median_shot):
            merged[-1].append(w)
        else:
            merged.append([w])

    transitions = _transition_sequence(style.get("transitions") or {"cut": 1.0}, len(merged))
    best_p = max(g[0].p for g in merged)
    for k, group in enumerate(merged):
        first, last = group[0], group[-1]
        gp = float(np.mean([w.p for w in group]))
        role = ("hook" if k == 0 else "resolution" if k == len(merged) - 1 and len(merged) > 2
                else "climax" if gp == best_p else "rising_action" if k > len(merged) / 3 else "context")
        why = (f"Style '{profile_name}': keep-probability {gp:.2f} "
               f"(model {np.mean([w.p_model for w in group]):.2f} · memory {np.mean([w.p_memory for w in group]):.2f})")
        if first.neighbor:
            n = first.neighbor
            why += f". Most similar past shot: {n['description']} — you {'kept' if n['kept'] else 'cut'} it."
        plan.rows.append({
            "segment_id": first.segment_id,
            "narrative_role": role,
            "transition_in": "cut" if k == 0 else transitions[k],
            "trim_start_ms": max(first.seg_start, first.trim_in),
            "trim_end_ms": min(last.seg_end, last.trim_out),
            "effects": dict(fx) or None,
            "edit_reasoning": why,
            "llm_confidence": round(gp, 3),
        })
        plan.planned_ms += int((plan.rows[-1]["trim_end_ms"] - plan.rows[-1]["trim_start_ms"]) / speed)
    return plan
