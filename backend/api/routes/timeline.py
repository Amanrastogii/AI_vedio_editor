"""
Manual video-editing endpoints: the clip/segment bin, timeline read/write
(trim, split, duplicate, join, reorder, effects), whole-timeline replace for
undo/redo, named versions, learned-style apply, and re-render.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import (
    ApplyStyleRequest, ClipRangeCreate, RenderRequest, RenderResponse, SegmentResponse, SplitRequest,
    StoryEntryCreate, StoryEntryResponse, StoryEntryUpdate, StoryReorderRequest, TimelineReplaceRequest,
    VersionCreate, VersionResponse,
)
from backend.config import settings
from backend.core.render_graph import normalize_effects
from backend.database.db import get_db
from backend.database.models import (
    Clip, NarrativeRole, ProjectStatus, Segment, SegmentType, StyleStatus, TransitionType, User,
)
from backend.database.repositories import (
    ClipRepository, ProjectRepository, SegmentRepository, StoryTimelineRepository, StyleRepository,
    TimelineVersionRepository,
)
from backend.storage import local_storage, s3_client

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])

MIN_PIECE_MS = 200


async def _get_owned_project(db: AsyncSession, project_id: uuid.UUID, user: User):
    project = await ProjectRepository(db).get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _not_processing(project) -> None:
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="The AI pipeline is running — wait for it to finish")


async def _url(key: Optional[str], bucket: Optional[str] = None) -> Optional[str]:
    if not key:
        return None
    if settings.LOCAL_MODE:
        return local_storage.public_url(key)
    return await s3_client.generate_presigned_url(bucket or settings.S3_BUCKET_ASSETS, key)


async def _thumb_url(key: str | None) -> str | None:
    return await _url(key)


async def entry_response(db: AsyncSession, e) -> StoryEntryResponse:
    """Full timeline entry, including thumbnail + raw source URL for live preview."""
    clip = await db.get(Clip, e.segment.clip_id) if e.segment else None
    return StoryEntryResponse(
        id=e.id,
        position=e.position_order,
        narrative_role=e.narrative_role.value,
        segment_id=e.segment_id,
        clip_id=clip.id if clip else None,
        thumbnail_url=await _url(e.segment.keyframe_s3_key) if e.segment else None,
        start_ms=e.segment.start_ms if e.segment else None,
        end_ms=e.segment.end_ms if e.segment else None,
        trim_start_ms=e.trim_start_ms,
        trim_end_ms=e.trim_end_ms,
        transition_in=e.transition_in.value,
        edit_reasoning=e.edit_reasoning,
        effects=e.effects,
        reframe_params=e.reframe_params,
        source_url=await _url(clip.s3_key, settings.S3_BUCKET_VIDEOS) if clip else None,
    )


async def _ordered_responses(db: AsyncSession, project_id: uuid.UUID) -> List[StoryEntryResponse]:
    return [await entry_response(db, e) for e in await StoryTimelineRepository(db).get_ordered(project_id)]


async def _owned_entry(db: AsyncSession, project_id: uuid.UUID, entry_id: uuid.UUID):
    entry = await StoryTimelineRepository(db).get(entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(status_code=404, detail="Timeline entry not found")
    return entry


def _bounds(entry) -> tuple:
    seg = entry.segment
    ts = entry.trim_start_ms if entry.trim_start_ms is not None else seg.start_ms
    te = entry.trim_end_ms if entry.trim_end_ms is not None else seg.end_ms
    return ts, te


# ── Bin ───────────────────────────────────────────────────────────────────────

@router.get("/segments", response_model=List[SegmentResponse])
async def list_segments(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All ingested segments for the project — the 'clip bin' for the manual editor."""
    await _get_owned_project(db, project_id, user)
    seg_repo = SegmentRepository(db)
    story_repo = StoryTimelineRepository(db)
    segments = await seg_repo.list_for_project(project_id)
    on_timeline_ids = {e.segment_id for e in await story_repo.get_ordered(project_id)}
    clips = {c.id: c for c in await ClipRepository(db).list_for_project(project_id)}

    out = []
    for s in segments:
        r = SegmentResponse.model_validate(s)
        r.keyframe_url = await _thumb_url(s.keyframe_s3_key)
        r.on_timeline = s.id in on_timeline_ids
        clip = clips.get(s.clip_id)
        r.source_url = await _url(clip.s3_key, settings.S3_BUCKET_VIDEOS) if clip else None
        out.append(r)
    return out


# ── Entry edits ───────────────────────────────────────────────────────────────

@router.patch("/story/{entry_id}", response_model=StoryEntryResponse)
async def update_story_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: StoryEntryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    repo = StoryTimelineRepository(db)
    entry = await _owned_entry(db, project_id, entry_id)

    updates = body.model_dump(exclude_unset=True)
    if "transition_in" in updates and updates["transition_in"] is not None:
        try:
            updates["transition_in"] = TransitionType(updates["transition_in"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown transition_in: {updates['transition_in']}")
    if updates.get("narrative_role") is not None:
        try:
            updates["narrative_role"] = NarrativeRole(updates["narrative_role"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown narrative_role: {updates['narrative_role']}")
    if "effects" in updates:
        updates["effects"] = normalize_effects(updates["effects"]) if updates["effects"] else None
    if "reframe_params" in updates and updates["reframe_params"] is not None:
        rp = updates["reframe_params"]
        mode = rp.get("mode")
        if mode not in (None, "smart", "center", "fit", "manual"):
            raise HTTPException(status_code=400, detail="reframe mode must be smart, center, fit or manual")
        clean = {"mode": mode} if mode else {}
        if mode == "manual":
            clean["x"] = max(0.0, min(1.0, float(rp.get("x", 0.5))))
        updates["reframe_params"] = clean or None
    if entry.segment and ("trim_start_ms" in updates or "trim_end_ms" in updates):
        seg = entry.segment
        ts, te = _bounds(entry)
        ts = updates.get("trim_start_ms", ts) if updates.get("trim_start_ms") is not None else ts
        te = updates.get("trim_end_ms", te) if updates.get("trim_end_ms") is not None else te
        ts = max(seg.start_ms, min(ts, seg.end_ms - MIN_PIECE_MS))
        te = min(seg.end_ms, max(te, ts + MIN_PIECE_MS))
        updates["trim_start_ms"], updates["trim_end_ms"] = ts, te
    if updates:
        await repo.update_entry(entry_id, **updates)
    db.expire_all()
    return await entry_response(db, await repo.get(entry_id))


@router.post("/story", response_model=StoryEntryResponse, status_code=201)
async def add_story_entry(
    project_id: uuid.UUID,
    body: StoryEntryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    seg_repo = SegmentRepository(db)
    segment = await seg_repo.get(body.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    repo = StoryTimelineRepository(db)
    kwargs = {"trim_start_ms": segment.start_ms, "trim_end_ms": segment.end_ms}
    if body.narrative_role:
        kwargs["narrative_role"] = NarrativeRole(body.narrative_role)
    if body.transition_in:
        kwargs["transition_in"] = TransitionType(body.transition_in)
    entry = await repo.add_entry(project_id, body.segment_id, **kwargs)
    entry = await repo.get(entry.id)
    return await entry_response(db, entry)


@router.post("/story/clip-range", response_model=StoryEntryResponse, status_code=201)
async def add_clip_range(
    project_id: uuid.UUID,
    body: ClipRangeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Put any range of a raw clip (default: the whole clip) on the timeline."""
    await _get_owned_project(db, project_id, user)
    clip = await ClipRepository(db).get(body.clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not clip.duration_ms:
        raise HTTPException(status_code=400, detail="Clip hasn't been ingested yet — prepare it for editing first")
    start = max(0, body.start_ms or 0)
    end = min(clip.duration_ms, body.end_ms or clip.duration_ms)
    if end - start < MIN_PIECE_MS:
        raise HTTPException(status_code=400, detail="Range is too short")

    seg = next((s for s in await SegmentRepository(db).list_for_clip(clip.id)
                if s.start_ms == start and s.end_ms == end), None)
    if not seg:
        seg = Segment(clip_id=clip.id, start_ms=start, end_ms=end, segment_type=SegmentType.GOOD,
                      keyframe_s3_key=clip.thumbnail_s3_key)
        db.add(seg)
        await db.commit()
        await db.refresh(seg)
    repo = StoryTimelineRepository(db)
    entry = await repo.add_entry(project_id, seg.id, trim_start_ms=start, trim_end_ms=end)
    return await entry_response(db, await repo.get(entry.id))


@router.delete("/story/{entry_id}", status_code=204)
async def delete_story_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    repo = StoryTimelineRepository(db)
    await _owned_entry(db, project_id, entry_id)
    await repo.delete_entry(entry_id)
    remaining = await repo.get_ordered(project_id)
    await repo.reorder(project_id, [e.id for e in remaining])


@router.post("/story/reorder", response_model=List[StoryEntryResponse])
async def reorder_story(
    project_id: uuid.UUID,
    body: StoryReorderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    repo = StoryTimelineRepository(db)
    current = await repo.get_ordered(project_id)
    current_ids = {e.id for e in current}
    if set(body.entry_ids) != current_ids:
        raise HTTPException(status_code=400, detail="entry_ids must be exactly the current timeline entries")
    await repo.reorder(project_id, body.entry_ids)
    db.expire_all()
    return await _ordered_responses(db, project_id)


@router.post("/story/{entry_id}/split", response_model=List[StoryEntryResponse])
async def split_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: SplitRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Razor tool: cut one clip into two at `at_ms` (source-clip time)."""
    await _get_owned_project(db, project_id, user)
    entry = await _owned_entry(db, project_id, entry_id)
    if not entry.segment:
        raise HTTPException(status_code=400, detail="This entry has no source segment")
    ts, te = _bounds(entry)
    if not (ts + MIN_PIECE_MS <= body.at_ms <= te - MIN_PIECE_MS):
        raise HTTPException(status_code=400, detail="Split point must be inside the clip (not at its edges)")
    repo = StoryTimelineRepository(db)
    await repo.update_entry(entry.id, trim_end_ms=body.at_ms)
    await repo.insert_after(
        project_id, entry.id, entry.segment_id, trim_start_ms=body.at_ms, trim_end_ms=te,
        narrative_role=entry.narrative_role, transition_in=TransitionType.CUT,
        effects=entry.effects, edit_reasoning=entry.edit_reasoning, reframe_params=entry.reframe_params,
    )
    db.expire_all()
    return await _ordered_responses(db, project_id)


@router.post("/story/{entry_id}/duplicate", response_model=List[StoryEntryResponse])
async def duplicate_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    entry = await _owned_entry(db, project_id, entry_id)
    if not entry.segment_id:
        raise HTTPException(status_code=400, detail="This entry has no source segment")
    await StoryTimelineRepository(db).insert_after(
        project_id, entry.id, entry.segment_id, trim_start_ms=entry.trim_start_ms,
        trim_end_ms=entry.trim_end_ms, narrative_role=entry.narrative_role,
        transition_in=entry.transition_in, effects=entry.effects, edit_reasoning=entry.edit_reasoning, reframe_params=entry.reframe_params,
    )
    db.expire_all()
    return await _ordered_responses(db, project_id)


@router.post("/story/{entry_id}/join-next", response_model=List[StoryEntryResponse])
async def join_with_next(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stitch a clip with the one after it — only when they're contiguous pieces of the same source clip."""
    await _get_owned_project(db, project_id, user)
    repo = StoryTimelineRepository(db)
    timeline = await repo.get_ordered(project_id)
    idx = next((i for i, e in enumerate(timeline) if e.id == entry_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Timeline entry not found")
    if idx + 1 >= len(timeline):
        raise HTTPException(status_code=400, detail="There's no clip after this one")
    a, b = timeline[idx], timeline[idx + 1]
    if not a.segment or not b.segment or a.segment.clip_id != b.segment.clip_id:
        raise HTTPException(status_code=400, detail="Only pieces of the same source clip can be joined")
    a_ts, a_te = _bounds(a)
    b_ts, b_te = _bounds(b)
    if abs(b_ts - a_te) > 150:
        raise HTTPException(status_code=400, detail="These pieces aren't contiguous in the source clip")
    seg_id = a.segment_id
    if a.segment_id != b.segment_id:
        span = Segment(clip_id=a.segment.clip_id, start_ms=min(a.segment.start_ms, b.segment.start_ms),
                       end_ms=max(a.segment.end_ms, b.segment.end_ms), segment_type=SegmentType.GOOD,
                       quality_score=a.segment.quality_score, engagement_score=a.segment.engagement_score,
                       keyframe_s3_key=a.segment.keyframe_s3_key)
        db.add(span)
        await db.commit()
        await db.refresh(span)
        seg_id = span.id
    await repo.update_entry(a.id, segment_id=seg_id, trim_start_ms=a_ts, trim_end_ms=b_te)
    await repo.delete_entry(b.id)
    await repo.reorder(project_id, [e.id for e in await repo.get_ordered(project_id)])
    db.expire_all()
    return await _ordered_responses(db, project_id)


@router.put("/story", response_model=List[StoryEntryResponse])
async def replace_timeline(
    project_id: uuid.UUID,
    body: TimelineReplaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Atomically replace the whole timeline — powers undo/redo."""
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    valid = {s.id for s in await SegmentRepository(db).list_for_project(project_id)}
    rows = [r.model_dump() for r in body.rows]
    if any(r["segment_id"] not in valid for r in rows):
        raise HTTPException(status_code=400, detail="Timeline references a segment outside this project")
    for r in rows:
        r["effects"] = normalize_effects(r["effects"]) if r.get("effects") else None
    await StoryTimelineRepository(db).replace_all(project_id, rows)
    db.expire_all()
    return await _ordered_responses(db, project_id)


# ── Versions ──────────────────────────────────────────────────────────────────

@router.get("/versions", response_model=List[VersionResponse])
async def list_versions(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    return [VersionResponse(id=v.id, label=v.label, entry_count=len(v.snapshot or []), created_at=v.created_at)
            for v in await TimelineVersionRepository(db).list_for_project(project_id)]


@router.post("/versions", response_model=VersionResponse, status_code=201)
async def save_version(
    project_id: uuid.UUID,
    body: VersionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    snap = StoryTimelineRepository.snapshot(await StoryTimelineRepository(db).get_ordered(project_id))
    v = await TimelineVersionRepository(db).create(project_id, label=body.label, snapshot=snap)
    return VersionResponse(id=v.id, label=v.label, entry_count=len(snap), created_at=v.created_at)


@router.post("/versions/{version_id}/restore", response_model=List[StoryEntryResponse])
async def restore_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    vrepo = TimelineVersionRepository(db)
    v = await vrepo.get(version_id)
    if not v or v.project_id != project_id:
        raise HTTPException(status_code=404, detail="Version not found")
    repo = StoryTimelineRepository(db)
    current = repo.snapshot(await repo.get_ordered(project_id))
    if current:
        await vrepo.create(project_id, label=f"Before restoring '{v.label}' (auto-saved)", snapshot=current)
    valid = {str(s.id) for s in await SegmentRepository(db).list_for_project(project_id)}
    await repo.replace_all(project_id, [r for r in v.snapshot if r.get("segment_id") in valid])
    db.expire_all()
    return await _ordered_responses(db, project_id)


@router.delete("/versions/{version_id}", status_code=204)
async def delete_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    vrepo = TimelineVersionRepository(db)
    v = await vrepo.get(version_id)
    if not v or v.project_id != project_id:
        raise HTTPException(status_code=404, detail="Version not found")
    await vrepo.delete(version_id)


# ── Learned style ─────────────────────────────────────────────────────────────

@router.post("/apply-style", response_model=List[StoryEntryResponse])
async def apply_style(
    project_id: uuid.UUID,
    body: ApplyStyleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-cut this project's timeline in an editor's learned style (current cut is saved as a version)."""
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    prof = await StyleRepository(db).get_profile(body.profile_id)
    if not prof or prof.user_id != user.id:
        raise HTTPException(status_code=404, detail="Style profile not found")
    if prof.status != StyleStatus.READY:
        raise HTTPException(status_code=400, detail="That style hasn't finished training yet")

    from backend.style.service import plan_timeline_with_style
    try:
        plan = await plan_timeline_with_style(project_id, body.profile_id,
                                              body.target_duration_sec or project.target_duration_sec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo = StoryTimelineRepository(db)
    current = repo.snapshot(await repo.get_ordered(project_id))
    if current:
        await TimelineVersionRepository(db).create(
            project_id, label=f"Before style '{prof.name}' (auto-saved)", snapshot=current)
    await repo.replace_all(project_id, plan["rows"])
    style = dict(project.target_style or {})
    style["style_profile_id"] = str(body.profile_id)
    from sqlalchemy import update as sa_update
    from backend.database.models import Project
    await db.execute(sa_update(Project).where(Project.id == project_id).values(target_style=style))
    await db.commit()
    db.expire_all()
    return await _ordered_responses(db, project_id)


# ── Render ────────────────────────────────────────────────────────────────────

@router.post("/render", response_model=RenderResponse)
async def render_timeline(
    project_id: uuid.UUID,
    body: RenderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-render outputs from the current story timeline — no AI stages re-run."""
    project = await _get_owned_project(db, project_id, user)
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Pipeline is already running")
    if not await StoryTimelineRepository(db).get_ordered(project_id):
        raise HTTPException(status_code=400, detail="The timeline is empty — add clips first")

    from backend.core.local_pipeline import rerender
    result = await rerender(str(project_id), body.output_formats or project.output_formats)
    return RenderResponse(**result)
