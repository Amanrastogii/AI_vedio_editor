"""Manual video-editing endpoints: the clip/segment bin, timeline read/write, and re-render."""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import (
    RenderRequest, RenderResponse, SegmentResponse, StoryEntryCreate,
    StoryEntryResponse, StoryEntryUpdate, StoryReorderRequest,
)
from backend.config import settings
from backend.database.db import get_db
from backend.database.models import ProjectStatus, TransitionType, User
from backend.database.repositories import ProjectRepository, SegmentRepository, StoryTimelineRepository
from backend.storage import local_storage, s3_client

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])


async def _get_owned_project(db: AsyncSession, project_id: uuid.UUID, user: User):
    project = await ProjectRepository(db).get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _thumb_url(key: str | None) -> str | None:
    if not key:
        return None
    if settings.LOCAL_MODE:
        return local_storage.public_url(key)
    return await s3_client.generate_presigned_url(settings.S3_BUCKET_ASSETS, key)


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

    out = []
    for s in segments:
        r = SegmentResponse.model_validate(s)
        r.keyframe_url = await _thumb_url(s.keyframe_s3_key)
        r.on_timeline = s.id in on_timeline_ids
        out.append(r)
    return out


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
    entry = await repo.get(entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(status_code=404, detail="Timeline entry not found")

    updates = body.model_dump(exclude_unset=True)
    if "transition_in" in updates and updates["transition_in"] is not None:
        try:
            updates["transition_in"] = TransitionType(updates["transition_in"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown transition_in: {updates['transition_in']}")
    if updates:
        await repo.update_entry(entry_id, **updates)
    entry = await repo.get(entry_id)
    return _entry_to_response(entry)


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
        from backend.database.models import NarrativeRole
        kwargs["narrative_role"] = NarrativeRole(body.narrative_role)
    if body.transition_in:
        kwargs["transition_in"] = TransitionType(body.transition_in)
    entry = await repo.add_entry(project_id, body.segment_id, **kwargs)
    entry = await repo.get(entry.id)
    return _entry_to_response(entry)


@router.delete("/story/{entry_id}", status_code=204)
async def delete_story_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    repo = StoryTimelineRepository(db)
    entry = await repo.get(entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(status_code=404, detail="Timeline entry not found")
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
    updated = await repo.get_ordered(project_id)
    return [_entry_to_response(e) for e in updated]


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

    from backend.core.local_pipeline import rerender
    result = await rerender(str(project_id), body.output_formats or project.output_formats)
    return RenderResponse(**result)


def _entry_to_response(e) -> StoryEntryResponse:
    return StoryEntryResponse(
        id=e.id,
        position=e.position_order,
        narrative_role=e.narrative_role.value,
        segment_id=e.segment_id,
        clip_id=e.segment.clip_id if e.segment else None,
        thumbnail_url=None,
        start_ms=e.segment.start_ms if e.segment else None,
        end_ms=e.segment.end_ms if e.segment else None,
        trim_start_ms=e.trim_start_ms,
        trim_end_ms=e.trim_end_ms,
        transition_in=e.transition_in.value,
        edit_reasoning=e.edit_reasoning,
    )
