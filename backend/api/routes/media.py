"""
Manual-editor media: import clips at any time (ingested instantly, no AI
pipeline needed), music / voice-over tracks, and text overlays.
"""
import asyncio
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.routes.timeline import _get_owned_project, _not_processing
from backend.api.schemas import (
    AudioTrackResponse, AudioTrackUpdate, ClipResponse, TextOverlayCreate, TextOverlayResponse,
    TextOverlayUpdate,
)
from backend.config import settings
from backend.core import real_ops
from backend.database.db import get_db
from backend.database.models import User
from backend.database.repositories import AudioTrackRepository, ClipRepository, TextOverlayRepository
from backend.storage import local_storage

router = APIRouter(prefix="/projects/{project_id}", tags=["media"])

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


def _require_local() -> None:
    if not settings.LOCAL_MODE:
        raise HTTPException(status_code=501, detail="Media import is available in LOCAL_MODE only")


def _ext_ok(filename: str, allowed: set) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'. Allowed: {sorted(allowed)}")
    return ext


# ── Video import ──────────────────────────────────────────────────────────────

@router.post("/media/import", response_model=ClipResponse, status_code=201)
async def import_clip(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a clip mid-edit: stored, probed and scene-detected immediately so it can go on the timeline."""
    _require_local()
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    _ext_ok(file.filename, VIDEO_EXT)

    clip_repo = ClipRepository(db)
    order = len(await clip_repo.list_for_project(project_id)) + 1
    clip_id = uuid.uuid4()
    key = local_storage.make_video_key(str(project_id), str(clip_id), file.filename)
    size = await local_storage.save_upload(key, file)
    if not (await asyncio.to_thread(real_ops.probe_metadata, local_storage._full(key))).get("duration_ms"):
        local_storage.delete(key)
        raise HTTPException(status_code=400, detail="That file isn't a readable video")
    clip = await clip_repo.create(
        project_id=project_id, filename=Path(file.filename).name, original_filename=file.filename,
        s3_key=key, s3_bucket="local", upload_order=order, file_size_bytes=size, id=clip_id,
    )
    from backend.core.local_pipeline import ingest_clip_for_editing
    await ingest_clip_for_editing(str(project_id), clip_id)
    db.expire_all()  # ingestion wrote via another session; don't touch `clip` attrs after this
    clip = await clip_repo.get(clip_id)
    r = ClipResponse.model_validate(clip)
    r.thumbnail_url = local_storage.public_url(clip.thumbnail_s3_key) if clip.thumbnail_s3_key else None
    r.source_url = local_storage.public_url(clip.s3_key)
    return r


@router.post("/media/ingest")
async def ingest_uploaded(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Prepare already-uploaded clips for manual editing without running the AI pipeline."""
    _require_local()
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    from backend.core.local_pipeline import ingest_clip_for_editing
    total = 0
    clips = await ClipRepository(db).list_for_project(project_id)
    for c in clips:
        total += await ingest_clip_for_editing(str(project_id), c.id)
    return {"clips": len(clips), "segments": total}


# ── Audio tracks ──────────────────────────────────────────────────────────────

def _audio_response(t) -> AudioTrackResponse:
    r = AudioTrackResponse.model_validate(t)
    r.url = local_storage.public_url(t.s3_key)
    return r


@router.get("/audio", response_model=List[AudioTrackResponse])
async def list_audio(project_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return [_audio_response(t) for t in await AudioTrackRepository(db).list_for_project(project_id)]


@router.post("/audio", response_model=AudioTrackResponse, status_code=201)
async def upload_audio(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a song / voice-over. Mixed under the video (with fades + ducking) at render time."""
    _require_local()
    await _get_owned_project(db, project_id, user)
    ext = _ext_ok(file.filename, AUDIO_EXT | VIDEO_EXT)  # a video's soundtrack works too
    track_id = uuid.uuid4()
    key = f"projects/{project_id}/audio/{track_id}{ext}"
    await local_storage.save_upload(key, file)
    meta = await asyncio.to_thread(real_ops.probe_metadata, local_storage._full(key))
    if not meta.get("has_audio"):
        local_storage.delete(key)
        raise HTTPException(status_code=400, detail="That file has no audio track")
    t = await AudioTrackRepository(db).create(
        project_id, id=track_id, original_filename=file.filename, s3_key=key,
        duration_ms=meta.get("duration_ms"), start_ms=0, source_offset_ms=0, volume=0.8,
        fade_in_ms=500, fade_out_ms=1500, loop=False, duck_original=1.0,
    )
    return _audio_response(t)


@router.patch("/audio/{track_id}", response_model=AudioTrackResponse)
async def update_audio(project_id: uuid.UUID, track_id: uuid.UUID, body: AudioTrackUpdate,
                       db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    repo = AudioTrackRepository(db)
    t = await repo.get(track_id)
    if not t or t.project_id != project_id:
        raise HTTPException(status_code=404, detail="Audio track not found")
    await repo.update(track_id, **body.model_dump(exclude_unset=True))
    db.expire_all()
    return _audio_response(await repo.get(track_id))


@router.delete("/audio/{track_id}", status_code=204)
async def delete_audio(project_id: uuid.UUID, track_id: uuid.UUID,
                       db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    repo = AudioTrackRepository(db)
    t = await repo.get(track_id)
    if not t or t.project_id != project_id:
        raise HTTPException(status_code=404, detail="Audio track not found")
    local_storage.delete(t.s3_key)
    await repo.delete(track_id)


# ── Text overlays ─────────────────────────────────────────────────────────────

@router.get("/text", response_model=List[TextOverlayResponse])
async def list_text(project_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return await TextOverlayRepository(db).list_for_project(project_id)


@router.post("/text", response_model=TextOverlayResponse, status_code=201)
async def add_text(project_id: uuid.UUID, body: TextOverlayCreate,
                   db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    if body.end_ms <= body.start_ms:
        raise HTTPException(status_code=400, detail="end_ms must be after start_ms")
    return await TextOverlayRepository(db).create(project_id, **body.model_dump())


@router.patch("/text/{overlay_id}", response_model=TextOverlayResponse)
async def update_text(project_id: uuid.UUID, overlay_id: uuid.UUID, body: TextOverlayUpdate,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    repo = TextOverlayRepository(db)
    o = await repo.get(overlay_id)
    if not o or o.project_id != project_id:
        raise HTTPException(status_code=404, detail="Text overlay not found")
    updates = body.model_dump(exclude_unset=True)
    s, e = updates.get("start_ms", o.start_ms), updates.get("end_ms", o.end_ms)
    if e <= s:
        raise HTTPException(status_code=400, detail="end_ms must be after start_ms")
    await repo.update(overlay_id, **updates)
    db.expire_all()
    return await repo.get(overlay_id)


@router.delete("/text/{overlay_id}", status_code=204)
async def delete_text(project_id: uuid.UUID, overlay_id: uuid.UUID,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    repo = TextOverlayRepository(db)
    o = await repo.get(overlay_id)
    if not o or o.project_id != project_id:
        raise HTTPException(status_code=404, detail="Text overlay not found")
    await repo.delete(overlay_id)
