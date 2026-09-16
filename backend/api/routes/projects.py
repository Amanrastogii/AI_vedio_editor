import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import ProjectCreate, ProjectResponse, StoryEntryResponse
from backend.config import settings
from backend.database.db import get_db
from backend.database.models import User
from backend.database.repositories import ClipRepository, ProjectRepository, StoryTimelineRepository
from backend.storage import local_storage, s3_client

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = ProjectRepository(db)
    project = await repo.create(
        user_id=user.id,
        title=body.title,
        target_duration_sec=body.target_duration_sec,
        target_style=body.target_style,
        output_formats=body.output_formats,
    )
    return project


@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = ProjectRepository(db)
    return await repo.list_for_user(user.id, limit=limit, offset=offset)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    await repo.delete(project_id)


@router.get("/{project_id}/story", response_model=List[StoryEntryResponse])
async def get_story(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    story_repo = StoryTimelineRepository(db)
    clip_repo = ClipRepository(db)
    timeline = await story_repo.get_ordered(project_id)

    responses = []
    for e in timeline:
        clip_id = e.segment.clip_id if e.segment else None
        thumb_key = None
        if e.segment and e.segment.keyframe_s3_key:
            thumb_key = e.segment.keyframe_s3_key
        thumb_url = None
        if thumb_key:
            thumb_url = (
                local_storage.public_url(thumb_key) if settings.LOCAL_MODE
                else await s3_client.generate_presigned_url(settings.S3_BUCKET_ASSETS, thumb_key)
            )
        responses.append(StoryEntryResponse(
            id=e.id,
            position=e.position_order,
            narrative_role=e.narrative_role.value,
            segment_id=e.segment_id,
            clip_id=clip_id,
            thumbnail_url=thumb_url,
            start_ms=e.segment.start_ms if e.segment else None,
            end_ms=e.segment.end_ms if e.segment else None,
            trim_start_ms=e.trim_start_ms,
            trim_end_ms=e.trim_end_ms,
            transition_in=e.transition_in.value,
            edit_reasoning=e.edit_reasoning,
        ))
    return responses
