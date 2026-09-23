"""
Editing assists: captions (speech-to-text), silence/filler removal, beat sync,
smart reframing, editor settings and background-job status.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.routes.timeline import _get_owned_project, _not_processing, _ordered_responses
from backend.database.db import get_db
from backend.database.models import User
from backend.editing import captions as cap
from backend.editing import service as editing
from backend.editing.cleanup import CleanupOptions

router = APIRouter(prefix="/projects/{project_id}", tags=["assist"])


# ── schemas ───────────────────────────────────────────────────────────────────

class SettingsPatch(BaseModel):
    captions: Optional[dict] = None
    reframe: Optional[dict] = None


class WordEdit(BaseModel):
    word_ids: List[uuid.UUID] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)


class CleanupRequest(BaseModel):
    remove_silence: bool = True
    min_silence_ms: int = Field(700, ge=200, le=10_000)
    padding_ms: int = Field(150, ge=0, le=1000)
    remove_fillers: bool = True
    extra_fillers: List[str] = []
    dry_run: bool = True


class BeatSyncRequest(BaseModel):
    track_id: uuid.UUID
    every: Optional[int] = Field(None, ge=1, le=16)
    dry_run: bool = True


def _job_view(j: dict) -> dict:
    return {k: j[k] for k in ("id", "kind", "status", "progress", "message", "result", "error")}


# ── settings ──────────────────────────────────────────────────────────────────

@router.get("/editor-settings")
async def get_editor_settings(project_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return await editing.get_settings(project_id)


@router.patch("/editor-settings")
async def patch_editor_settings(project_id: uuid.UUID, body: SettingsPatch, db: AsyncSession = Depends(get_db),
                                user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return await editing.update_settings(project_id, body.model_dump(exclude_none=True))


# ── jobs ──────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(project_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return [_job_view(j) for j in editing.project_jobs(str(project_id))]


@router.get("/jobs/{job_id}")
async def get_job(project_id: uuid.UUID, job_id: str, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    j = editing.get_job(job_id)
    if not j or j["project_id"] != str(project_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_view(j)


# ── captions ──────────────────────────────────────────────────────────────────

@router.post("/transcribe", status_code=202)
async def transcribe(project_id: uuid.UUID, force: bool = False, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Transcribe the project's clips in the background (cached per clip). Poll /jobs/{id}."""
    await _get_owned_project(db, project_id, user)
    if not editing.transcription_available():
        raise HTTPException(status_code=501, detail="Speech-to-text isn't installed — run: pip install faster-whisper")
    job = editing.start_job(str(project_id), "transcribe",
                            lambda progress: editing.ensure_transcripts(project_id, progress=progress, force=force))
    return _job_view(job)


@router.get("/captions")
async def get_captions(project_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Caption cues in sequence time (they follow every timeline edit) + transcription status."""
    await _get_owned_project(db, project_id, user)
    cues = await editing.caption_cues(project_id)
    return {"cues": [c.to_dict() for c in cues], "status": await editing.transcription_status(project_id),
            "settings": (await editing.get_settings(project_id))["captions"]}


@router.put("/captions/words")
async def edit_caption_words(project_id: uuid.UUID, body: WordEdit, db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """Correct caption text: replaces those transcript words with `text` (re-timed across the same span)."""
    await _get_owned_project(db, project_id, user)
    try:
        n = await editing.replace_words(project_id, body.word_ids, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"words": n}


@router.get("/captions.srt", response_class=PlainTextResponse)
async def captions_srt(project_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return PlainTextResponse(cap.to_srt(await editing.caption_cues(project_id)), media_type="application/x-subrip",
                             headers={"Content-Disposition": 'attachment; filename="captions.srt"'})


@router.get("/captions.vtt", response_class=PlainTextResponse)
async def captions_vtt(project_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    return PlainTextResponse(cap.to_vtt(await editing.caption_cues(project_id)), media_type="text/vtt",
                             headers={"Content-Disposition": 'attachment; filename="captions.vtt"'})


# ── silence & filler removal ──────────────────────────────────────────────────

@router.post("/cleanup")
async def cleanup(project_id: uuid.UUID, body: CleanupRequest, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Plan (dry_run) or apply silence + filler-word removal. Applying auto-saves the current cut as a version."""
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    if body.remove_fillers and editing.transcription_available():
        await editing.ensure_transcripts(project_id)   # fillers need word timings (cached after the first run)
    opts = CleanupOptions(remove_silence=body.remove_silence, min_silence_ms=body.min_silence_ms,
                          padding_ms=body.padding_ms, remove_fillers=body.remove_fillers,
                          extra_fillers=body.extra_fillers)
    rows, plan = await editing.plan_project_cleanup(project_id, opts)
    if not rows:
        raise HTTPException(status_code=400, detail="The timeline is empty")
    out = {"stats": plan.stats(), "applied": False, "transcription": await editing.transcription_status(project_id)}
    if not body.dry_run and plan.removed_ms > 0:
        await editing.apply_rows(project_id, plan.rows, "Before removing silences/fillers (auto-saved)")
        db.expire_all()
        out["applied"] = True
        out["timeline"] = [e.model_dump(mode="json") for e in await _ordered_responses(db, project_id)]
    return out


# ── beats ─────────────────────────────────────────────────────────────────────

@router.post("/audio/{track_id}/beats")
async def analyze_beats(project_id: uuid.UUID, track_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    await _get_owned_project(db, project_id, user)
    try:
        a = await editing.ensure_beats(project_id, track_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"bpm": a["bpm"], "beat_count": len(a["beats_ms"]), "downbeat_phase": a["downbeat_phase"]}


@router.get("/audio/{track_id}/beat-grid")
async def get_beat_grid(project_id: uuid.UUID, track_id: uuid.UUID, every: int = 1,
                        db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Beat times in sequence ms for a placed music bed (for timeline ticks / snapping)."""
    await _get_owned_project(db, project_id, user)
    try:
        return {"beats_ms": await editing.beat_grid(project_id, track_id, max(1, every))}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/beat-sync")
async def beat_sync(project_id: uuid.UUID, body: BeatSyncRequest, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    project = await _get_owned_project(db, project_id, user)
    _not_processing(project)
    try:
        rows, plan, analysis = await editing.plan_project_beat_sync(project_id, body.track_id, body.every)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = {"stats": plan.stats(), "bpm": analysis["bpm"], "applied": False}
    if not body.dry_run:
        await editing.apply_rows(project_id, plan.rows, "Before beat sync (auto-saved)")
        db.expire_all()
        out["applied"] = True
        out["timeline"] = [e.model_dump(mode="json") for e in await _ordered_responses(db, project_id)]
    return out


# ── reframing ─────────────────────────────────────────────────────────────────

@router.get("/reframe/tracks")
async def reframe_tracks(project_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Per-clip subject path (computed once per clip, then cached) — the preview uses it for vertical framing."""
    await _get_owned_project(db, project_id, user)
    tracks = await editing.subject_tracks(project_id)
    return {cid: {"source": t.get("source"), "points": t.get("points", [])} for cid, t in tracks.items()}
