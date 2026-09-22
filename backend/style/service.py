"""
Async orchestration + persistence for style learning.

    train_profile()            analyze new examples (cached) → aggregate style → fit selector →
                               rebuild retrieval memory → write summary. Runs as a background task.
    plan_timeline_with_style() featurize a project's footage → timeline rows in the learned style.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.models import StyleStatus
from backend.database.repositories import ClipRepository, SegmentRepository, StyleRepository
from backend.storage import local_storage
from backend.style import features, learner, planner
from backend.style import profile as style_profile
from backend.style.analyzer import analyze_example

logger = logging.getLogger(__name__)

_training: set = set()          # profile ids with a training task in flight


def style_asset_key(profile_id: str, example_id: str, asset_id: str, filename: str) -> str:
    ext = Path(filename).suffix.lower() or ".mp4"
    return f"styles/{profile_id}/{example_id}/{asset_id}{ext}"


async def _set_stage(profile_id: uuid.UUID, stage: str, **extra) -> None:
    async with AsyncSessionLocal() as session:
        repo = StyleRepository(session)
        p = await repo.get_profile(profile_id)
        metrics = dict((p.metrics or {}) if p else {})
        metrics["stage"] = stage
        await repo.update_profile(profile_id, metrics=metrics, **extra)


def is_training(profile_id: uuid.UUID) -> bool:
    return str(profile_id) in _training


def launch_training(profile_id: uuid.UUID, force_reanalyze: bool = False) -> bool:
    if is_training(profile_id):
        return False
    _training.add(str(profile_id))
    asyncio.create_task(_train_guarded(profile_id, force_reanalyze))
    return True


async def _train_guarded(profile_id: uuid.UUID, force: bool) -> None:
    try:
        await train_profile(profile_id, force)
    except Exception as exc:  # noqa: BLE001
        logger.exception("style training failed for %s", profile_id)
        await _set_stage(profile_id, "failed", status=StyleStatus.FAILED, error_message=str(exc)[:1000])
    finally:
        _training.discard(str(profile_id))


async def train_profile(profile_id: uuid.UUID, force_reanalyze: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        prof = await StyleRepository(session).get_profile(profile_id)
    if not prof:
        return
    examples = [e for e in prof.examples if any(a.kind == "edited" for a in e.assets)
                and any(a.kind == "raw" for a in e.assets)]
    if not examples:
        raise ValueError("Add at least one example with a finished edit AND its raw clips.")
    await _set_stage(profile_id, "analyzing examples", status=StyleStatus.ANALYZING, error_message=None)

    analyses, ex_ids = [], []
    for n, ex in enumerate(examples, start=1):
        cached = ex.analysis or {}
        if (not force_reanalyze and ex.status == StyleStatus.READY
                and cached.get("feature_version") == features.FEATURE_VERSION):
            analyses.append(cached)
            ex_ids.append(ex.id)
            continue
        async with AsyncSessionLocal() as session:
            await StyleRepository(session).update_example(ex.id, status=StyleStatus.ANALYZING, error_message=None)
        edited = next(a for a in ex.assets if a.kind == "edited")
        raws = sorted([a for a in ex.assets if a.kind == "raw"], key=lambda a: a.created_at)

        def progress(msg: str, _n=n) -> None:
            logger.info("style %s example %d/%d: %s", profile_id, _n, len(examples), msg)

        try:
            await _set_stage(profile_id, f"analyzing example {n}/{len(examples)}: {ex.title}")
            analysis = await asyncio.to_thread(
                analyze_example, local_storage._full(edited.s3_key),
                [local_storage._full(a.s3_key) for a in raws], [a.original_filename for a in raws], progress)
        except Exception as exc:  # noqa: BLE001 — one bad example shouldn't sink the profile
            logger.exception("analysis failed for example %s", ex.id)
            async with AsyncSessionLocal() as session:
                await StyleRepository(session).update_example(
                    ex.id, status=StyleStatus.FAILED, error_message=str(exc)[:1000])
            continue
        async with AsyncSessionLocal() as session:
            await StyleRepository(session).update_example(ex.id, status=StyleStatus.READY, analysis=analysis)
        analyses.append(analysis)
        ex_ids.append(ex.id)

    if not analyses:
        raise ValueError("None of the examples could be analyzed — check that the files are valid videos.")
    if all((a.get("coverage") or 0) < 0.05 for a in analyses):
        raise ValueError("The finished edits could not be matched to the raw clips. Make sure each example's "
                         "raw clips are the footage that edit was actually cut from.")

    await _set_stage(profile_id, "training", status=StyleStatus.TRAINING)
    style = style_profile.aggregate_style(analyses)
    model_json, metrics, mem_rows = await asyncio.to_thread(
        style_profile.train, analyses, [str(i) for i in ex_ids])
    for r in mem_rows:
        r["example_id"] = uuid.UUID(r["example_id"])
    summary = style_profile.summarize(prof.name, style, metrics)
    narrated = await _narrate(prof.name, style, metrics, mem_rows)
    if narrated:
        summary = narrated + "\n\n" + summary

    metrics["stage"] = "ready"
    async with AsyncSessionLocal() as session:
        repo = StyleRepository(session)
        await repo.replace_memories(profile_id, mem_rows)
        await repo.update_profile(
            profile_id, status=StyleStatus.READY, style=style, model=model_json, metrics=metrics,
            summary=summary, error_message=None, trained_at=datetime.now(timezone.utc))


async def _narrate(name: str, style: Dict, metrics: Dict, mem_rows: List[Dict]) -> Optional[str]:
    """
    Optional generation step (the "G" in RAG): when ANTHROPIC_API_KEY is set,
    Claude turns the learned statistics + a sample of retrieved decisions into
    a short editor-facing description. Silent no-op without a key.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import json

        import anthropic
        kept = [m["description"] for m in mem_rows if m["kept"]][:12]
        cut = [m["description"] for m in mem_rows if not m["kept"]][:12]
        prompt = (
            f"You are describing a video editor's personal editing style, named '{name}', learned from their "
            "past work. Using ONLY the measured data below, write 3-5 plain sentences an editor would "
            "recognize as their style (pacing, what they keep vs cut, story order, look, sound). "
            "Do not invent anything not supported by the data.\n\n"
            f"Aggregate style:\n{json.dumps(style, indent=1, default=str)}\n\n"
            f"Top model weights: {json.dumps(metrics.get('importance', []))}\n"
            f"Examples of shots they KEPT: {kept}\nExamples of shots they CUT: {cut}"
        )
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=settings.CLAUDE_STORY_MODEL, max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
    except Exception as e:  # noqa: BLE001 — narration is optional
        logger.warning("style narration via Claude failed: %s", e)
        return None


# ── Apply ─────────────────────────────────────────────────────────────────────

async def plan_timeline_with_style(project_id: uuid.UUID, profile_id: uuid.UUID,
                                   target_duration_sec: Optional[int] = None) -> Dict:
    async with AsyncSessionLocal() as session:
        repo = StyleRepository(session)
        prof = await repo.get_profile(profile_id)
        if not prof or prof.status != StyleStatus.READY or not prof.model:
            raise ValueError("Style profile is not trained yet.")
        memories = await repo.list_memories(profile_id)
        clips = await ClipRepository(session).list_for_project(project_id)
        seg_repo = SegmentRepository(session)
        clip_segments = {c.id: await seg_repo.list_for_clip(c.id) for c in clips}

    def featurize() -> tuple:
        windows: List[planner.Window] = []
        raw_total = 0
        for clip in clips:
            segs = clip_segments.get(clip.id) or []
            path = local_storage._full(clip.s3_key)
            if not segs or not path.exists():
                continue
            ca = features.analyze_clip(path)
            raw_total += ca.frames.duration_ms
            spans = [(s, w) for s in segs for w in features.windows_for_shot(s.start_ms, s.end_ms)
                     if w[1] - w[0] >= 400]
            if not spans:
                continue
            X = features.window_features(ca, [w for _, w in spans])
            for (seg, w), f in zip(spans, X):
                windows.append(planner.Window(
                    segment_id=str(seg.id), clip_order=clip.upload_order, start_ms=w[0], end_ms=w[1],
                    seg_start=seg.start_ms, seg_end=seg.end_ms, features=f.tolist()))
        return windows, raw_total

    windows, raw_total = await asyncio.to_thread(featurize)
    if not windows:
        raise ValueError("No analyzable footage in this project yet.")

    sc = learner.Standardizer.from_dict(prof.model["scaler"])
    memory = learner.Memory(
        sc.transform(np.array([m.features for m in memories], dtype=np.float64)) if memories
        else np.zeros((0, len(features.FEATURE_NAMES))),
        np.array([1.0 if m.kept else 0.0 for m in memories]),
        [m.decision or {} for m in memories], [m.description or "" for m in memories])
    plan = await asyncio.to_thread(planner.plan_timeline, windows, prof.model, memory, prof.style or {},
                                   prof.name, raw_total, target_duration_sec)
    return {
        "rows": plan.rows,
        "summary": f"{len(plan.rows)} clips · {plan.planned_ms / 1000:.0f}s of {raw_total / 1000:.0f}s footage "
                   f"(target {plan.target_ms / 1000:.0f}s) in style '{prof.name}'",
        "target_ms": plan.target_ms,
        "planned_ms": plan.planned_ms,
        "candidates": plan.candidates,
    }
