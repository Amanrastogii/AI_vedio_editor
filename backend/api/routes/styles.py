"""Editor style profiles: upload (finished edit + raw clips) examples, train, inspect."""
import asyncio
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import (
    StyleAssetResponse, StyleExampleResponse, StyleProfileCreate, StyleProfileResponse,
)
from backend.config import settings
from backend.core import real_ops
from backend.database.db import get_db
from backend.database.models import StyleStatus, User
from backend.database.repositories import StyleRepository
from backend.storage import local_storage
from backend.style import service

router = APIRouter(prefix="/styles", tags=["styles"])

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _example_response(e) -> StyleExampleResponse:
    analysis = dict(e.analysis) if e.analysis else None
    if analysis:
        analysis.pop("samples", None)       # training rows — large, internal
    return StyleExampleResponse(
        id=e.id, title=e.title, status=e.status.value, analysis=analysis, error_message=e.error_message,
        assets=[StyleAssetResponse.model_validate(a) for a in sorted(e.assets, key=lambda a: (a.kind != "edited",
                                                                                              a.created_at))],
        created_at=e.created_at,
    )


def _profile_response(p, with_examples: bool = False) -> StyleProfileResponse:
    status = p.status.value
    if service.is_training(p.id) and status not in ("analyzing", "training"):
        status = "analyzing"
    return StyleProfileResponse(
        id=p.id, name=p.name, description=p.description, status=status, style=p.style,
        metrics=p.metrics, summary=p.summary, error_message=p.error_message, trained_at=p.trained_at,
        created_at=p.created_at, example_count=len(p.examples),
        examples=[_example_response(e) for e in sorted(p.examples, key=lambda e: e.created_at)]
        if with_examples else None,
    )


async def _owned(db: AsyncSession, profile_id: uuid.UUID, user: User):
    p = await StyleRepository(db).get_profile(profile_id)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="Style profile not found")
    return p


async def _save_asset(repo: StyleRepository, profile_id, example_id, kind: str, f: UploadFile):
    ext = Path(f.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(status_code=400, detail=f"'{f.filename}' is not a supported video ({sorted(VIDEO_EXT)})")
    asset_id = uuid.uuid4()
    key = service.style_asset_key(str(profile_id), str(example_id), str(asset_id), f.filename)
    await local_storage.save_upload(key, f)
    meta = await asyncio.to_thread(real_ops.probe_metadata, local_storage._full(key))
    if not meta.get("duration_ms"):
        local_storage.delete(key)
        raise HTTPException(status_code=400, detail=f"'{f.filename}' isn't a readable video")
    return await repo.add_asset(example_id, kind, f.filename, key, id=asset_id,
                                duration_ms=meta.get("duration_ms"), width=meta.get("width"),
                                height=meta.get("height"))


@router.post("", response_model=StyleProfileResponse, status_code=201)
async def create_profile(body: StyleProfileCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    p = await StyleRepository(db).create_profile(user.id, body.name, body.description)
    return _profile_response(await StyleRepository(db).get_profile(p.id))


@router.get("", response_model=List[StyleProfileResponse])
async def list_profiles(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return [_profile_response(p) for p in await StyleRepository(db).list_profiles(user.id)]


@router.get("/{profile_id}", response_model=StyleProfileResponse)
async def get_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    return _profile_response(await _owned(db, profile_id, user), with_examples=True)


@router.patch("/{profile_id}", response_model=StyleProfileResponse)
async def update_profile(profile_id: uuid.UUID, body: StyleProfileCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    await _owned(db, profile_id, user)
    repo = StyleRepository(db)
    await repo.update_profile(profile_id, name=body.name, description=body.description)
    db.expire_all()
    return _profile_response(await repo.get_profile(profile_id), with_examples=True)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    await _owned(db, profile_id, user)
    if service.is_training(profile_id):
        raise HTTPException(status_code=409, detail="Training in progress — try again when it finishes")
    await StyleRepository(db).delete_profile(profile_id)
    shutil.rmtree(local_storage.ROOT / "styles" / str(profile_id), ignore_errors=True)


@router.post("/{profile_id}/examples", response_model=StyleExampleResponse, status_code=201)
async def add_example(
    profile_id: uuid.UUID,
    edited: UploadFile = File(..., description="The finished, edited video"),
    raw: List[UploadFile] = File(..., description="The raw clips it was cut from"),
    title: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One training example = a finished edit + the raw footage it was made from."""
    if not settings.LOCAL_MODE:
        raise HTTPException(status_code=501, detail="Style training is available in LOCAL_MODE only")
    await _owned(db, profile_id, user)
    if service.is_training(profile_id):
        raise HTTPException(status_code=409, detail="Training in progress — add examples when it finishes")
    if not raw:
        raise HTTPException(status_code=400, detail="Upload at least one raw clip")
    repo = StyleRepository(db)
    ex = await repo.create_example(profile_id, (title or Path(edited.filename or "Example").stem)[:255])
    try:
        await _save_asset(repo, profile_id, ex.id, "edited", edited)
        for f in raw:
            await _save_asset(repo, profile_id, ex.id, "raw", f)
    except HTTPException:
        await repo.delete_example(ex.id)
        shutil.rmtree(local_storage.ROOT / "styles" / str(profile_id) / str(ex.id), ignore_errors=True)
        raise
    return _example_response(await repo.get_example(ex.id))


@router.delete("/{profile_id}/examples/{example_id}", status_code=204)
async def delete_example(profile_id: uuid.UUID, example_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    await _owned(db, profile_id, user)
    if service.is_training(profile_id):
        raise HTTPException(status_code=409, detail="Training in progress — try again when it finishes")
    repo = StyleRepository(db)
    ex = await repo.get_example(example_id)
    if not ex or ex.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Example not found")
    await repo.delete_example(example_id)
    shutil.rmtree(local_storage.ROOT / "styles" / str(profile_id) / str(example_id), ignore_errors=True)


@router.post("/{profile_id}/train", response_model=StyleProfileResponse, status_code=202)
async def train(profile_id: uuid.UUID, reanalyze: bool = False, db: AsyncSession = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Analyze new examples and (re)train the style. Runs in the background — poll GET /styles/{id}."""
    p = await _owned(db, profile_id, user)
    if not p.examples:
        raise HTTPException(status_code=400, detail="Add at least one example first")
    if not service.launch_training(profile_id, force_reanalyze=reanalyze):
        raise HTTPException(status_code=409, detail="Training is already running")
    await StyleRepository(db).update_profile(profile_id, status=StyleStatus.ANALYZING, error_message=None)
    db.expire_all()
    return _profile_response(await StyleRepository(db).get_profile(profile_id), with_examples=True)
