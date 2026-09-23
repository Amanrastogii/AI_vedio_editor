import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import ProjectCreate, ProjectResponse, ProjectUpdate, StoryEntryResponse
from backend.config import settings
from backend.database.db import get_db
from backend.database.models import Project, User
from backend.database.repositories import ProjectRepository, StoryTimelineRepository

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

    from backend.api.routes.timeline import entry_response
    timeline = await StoryTimelineRepository(db).get_ordered(project_id)
    return [await entry_response(db, e) for e in timeline]


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit title / target duration / style (e.g. {"style_profile_id": ...}) / output formats."""
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = body.model_dump(exclude_unset=True)
    if "output_formats" in updates and updates["output_formats"] is not None:
        bad = [f for f in updates["output_formats"] if f not in settings.OUTPUT_FORMATS]
        if bad or not updates["output_formats"]:
            raise HTTPException(status_code=400, detail=f"Unknown output format(s): {bad}")
    if updates:
        await db.execute(update(Project).where(Project.id == project_id).values(**updates))
        await db.commit()
    db.expire_all()
    return await repo.get(project_id)
