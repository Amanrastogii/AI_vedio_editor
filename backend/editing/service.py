"""
Async orchestration + persistence for the editing tools. Heavy analysis runs
off the event loop and is cached on the clip (Clip.ingestion_metadata) or the
audio track (AudioTrack.analysis), so each piece of media is analysed once.
"""
import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sqlalchemy import delete, update

from backend.audio import beats as beats_mod
from backend.audio import silence as silence_mod
from backend.audio import transcribe as stt
from backend.config import settings
from backend.core import real_ops
from backend.database.db import AsyncSessionLocal
from backend.database.models import Clip, Project, Transcript
from backend.database.repositories import (
    AudioTrackRepository, ClipRepository, StoryTimelineRepository, TimelineVersionRepository,
    TranscriptRepository,
)
from backend.editing import captions as cap
from backend.editing import settings as ed_settings
from backend.editing.beatsync import auto_every, plan_beat_sync
from backend.editing.cleanup import CleanupOptions, plan_cleanup
from backend.editing.layout import place, rows_from_entries, total_ms
from backend.editing.reframe import TRACK_VERSION, crop_filter, track_subject
from backend.storage import local_storage

logger = logging.getLogger(__name__)

# ── background jobs (in-process, LOCAL_MODE) ──────────────────────────────────
_jobs: Dict[str, dict] = {}


def start_job(project_id: str, kind: str, coro_factory: Callable[[Callable[[str, float], None]], "asyncio.Future"]) -> dict:
    """Run a long task in the background; progress is polled via get_job()."""
    for j in _jobs.values():
        if j["project_id"] == project_id and j["kind"] == kind and j["status"] == "running":
            return j
    job = {"id": uuid.uuid4().hex, "project_id": project_id, "kind": kind, "status": "running",
           "progress": 0.0, "message": "starting", "result": None, "error": None, "started": time.time()}
    _jobs[job["id"]] = job

    def progress(msg: str, pct: float) -> None:
        job["message"], job["progress"] = msg, round(max(0.0, min(1.0, pct)), 3)

    async def runner():
        try:
            job["result"] = await coro_factory(progress)
            job["status"], job["progress"], job["message"] = "done", 1.0, "done"
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s failed", job["id"])
            job["status"], job["error"] = "failed", str(exc)[:500]

    asyncio.create_task(runner())
    return job


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def project_jobs(project_id: str) -> List[dict]:
    return [j for j in _jobs.values() if j["project_id"] == project_id][-10:]


# ── settings ──────────────────────────────────────────────────────────────────

async def get_settings(project_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as s:
        p = await s.get(Project, project_id)
        return ed_settings.resolve(p.editor_settings if p else None)


async def update_settings(project_id: uuid.UUID, patch: dict) -> dict:
    async with AsyncSessionLocal() as s:
        p = await s.get(Project, project_id)
        merged = ed_settings.merge(p.editor_settings if p else None, patch)
        await s.execute(update(Project).where(Project.id == project_id).values(editor_settings=merged))
        await s.commit()
        return merged


# ── clip metadata cache helpers ───────────────────────────────────────────────

async def _clip_meta_update(clip_id: uuid.UUID, **values) -> None:
    async with AsyncSessionLocal() as s:
        clip = await s.get(Clip, clip_id)
        meta = dict(clip.ingestion_metadata or {})
        meta.update(values)
        await s.execute(update(Clip).where(Clip.id == clip_id).values(ingestion_metadata=meta))
        await s.commit()


async def _project_clips(project_id: uuid.UUID) -> List[Clip]:
    async with AsyncSessionLocal() as s:
        return await ClipRepository(s).list_for_project(project_id)


# ── transcription ─────────────────────────────────────────────────────────────

def transcription_available() -> bool:
    return stt.available()


async def ensure_transcripts(project_id: uuid.UUID, clip_ids: Optional[List[uuid.UUID]] = None,
                             progress: Callable[[str, float], None] = lambda m, p: None,
                             force: bool = False) -> dict:
    """Transcribe clips that haven't been yet (cached per clip). Clips without audio are skipped."""
    if not stt.available():
        raise RuntimeError("Speech-to-text isn't available — install faster-whisper (pip install faster-whisper) "
                           "or set TRANSCRIPTION_PROVIDER.")
    clips = [c for c in await _project_clips(project_id) if clip_ids is None or c.id in clip_ids]
    done, words_total = 0, 0
    for i, clip in enumerate(clips):
        meta = clip.ingestion_metadata or {}
        if meta.get("transcribed") and not force:
            done += 1
            continue
        path = local_storage._full(clip.s3_key)
        progress(f"Transcribing {clip.original_filename} ({i + 1}/{len(clips)})", i / max(1, len(clips)))
        info = await asyncio.to_thread(real_ops.probe_metadata, path) if path.exists() else {}
        words = []
        if info.get("has_audio"):
            words = await asyncio.to_thread(stt.transcribe_words, path)
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Transcript).where(Transcript.clip_id == clip.id))
            await s.commit()
            if words:
                await TranscriptRepository(s).bulk_create([{
                    "clip_id": clip.id, "speaker_id": "SPEAKER_00", "word": w.word, "start_ms": w.start_ms,
                    "end_ms": w.end_ms, "confidence": w.confidence, "is_filler": w.is_filler,
                } for w in words])
        await _clip_meta_update(clip.id, transcribed=True, transcribed_with=f"faster-whisper:{settings.WHISPER_MODEL}",
                                word_count=len(words), has_audio=bool(info.get("has_audio")))
        done += 1
        words_total += len(words)
    progress("Transcription complete", 1.0)
    return {"clips": len(clips), "transcribed": done, "new_words": words_total}


async def words_by_clip(project_id: uuid.UUID) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    async with AsyncSessionLocal() as s:
        repo = TranscriptRepository(s)
        for c in await ClipRepository(s).list_for_project(project_id):
            if (c.ingestion_metadata or {}).get("transcribed"):
                out[str(c.id)] = [{"id": str(w.id), "word": w.word, "start_ms": w.start_ms, "end_ms": w.end_ms,
                                   "is_filler": w.is_filler, "is_silence": w.is_silence}
                                  for w in await repo.get_for_clip(c.id)]
    return out


async def transcription_status(project_id: uuid.UUID) -> dict:
    clips = await _project_clips(project_id)
    done = [c for c in clips if (c.ingestion_metadata or {}).get("transcribed")]
    return {"available": stt.available(), "model": settings.WHISPER_MODEL, "clips": len(clips),
            "transcribed": len(done), "words": sum((c.ingestion_metadata or {}).get("word_count", 0) for c in done)}


async def replace_words(project_id: uuid.UUID, word_ids: List[uuid.UUID], text: str) -> int:
    """Correct a caption: the given words (one clip, contiguous) become `text`, re-timed evenly across the same span."""
    async with AsyncSessionLocal() as s:
        rows = [await s.get(Transcript, wid) for wid in word_ids]
        rows = [r for r in rows if r is not None]
        if not rows:
            raise ValueError("No such words")
        clip_ids = {r.clip_id for r in rows}
        clip = await s.get(Clip, next(iter(clip_ids)))
        if len(clip_ids) != 1 or not clip or clip.project_id != project_id:
            raise ValueError("Words must belong to one clip of this project")
        rows.sort(key=lambda r: r.start_ms)
        start, end = rows[0].start_ms, rows[-1].end_ms
        new = [w for w in text.split() if w]
        for r in rows:
            await s.delete(r)
        span = max(1, end - start)
        for i, w in enumerate(new):
            a = start + span * i // len(new)
            b = start + span * (i + 1) // len(new)
            s.add(Transcript(clip_id=clip.id, speaker_id="SPEAKER_00", word=w, start_ms=a, end_ms=max(a + 40, b),
                             confidence=1.0, is_filler=stt.is_filler(w)))
        await s.commit()
    return len(new)


# ── captions ──────────────────────────────────────────────────────────────────

async def _timeline_rows(project_id: uuid.UUID) -> List[dict]:
    async with AsyncSessionLocal() as s:
        entries = await StoryTimelineRepository(s).get_ordered(project_id)
        rows = rows_from_entries(entries)
        for r, e in zip(rows, [e for e in entries if e.segment]):
            r.update({"segment_id": str(e.segment_id), "narrative_role": e.narrative_role.value,
                      "edit_reasoning": e.edit_reasoning, "reframe_params": e.reframe_params})
        return rows


async def caption_cues(project_id: uuid.UUID, cfg: Optional[dict] = None) -> List[cap.Cue]:
    cfg = cfg or (await get_settings(project_id))["captions"]
    rows = await _timeline_rows(project_id)
    words = await words_by_clip(project_id)
    seq = cap.sequence_words(place(rows), words, cfg["hide_fillers"])
    return cap.build_cues(seq, cfg["max_chars"], cfg["max_lines"], cfg["uppercase"])


async def write_caption_ass(project_id: uuid.UUID, width: int, height: int, out: Path) -> Optional[Path]:
    """ASS file for burning captions into a render, or None when captions are off / empty."""
    cfg = (await get_settings(project_id))["captions"]
    if not cfg["enabled"]:
        return None
    cues = await caption_cues(project_id, cfg)
    if not cues:
        return None
    from backend.core.render_graph import find_font
    font = find_font()
    family = "Arial" if not font or "arial" in font.name.lower() else "DejaVu Sans"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(cap.to_ass(cues, cfg, width, height, family), encoding="utf-8")
    return out


# ── silence & filler cleanup ──────────────────────────────────────────────────

async def silences_by_clip(project_id: uuid.UUID, clip_ids: List[str], noise_db: float = -35.0,
                           min_ms: int = 300) -> Dict[str, list]:
    out: Dict[str, list] = {}
    key = f"silences:{noise_db}:{min_ms}"
    for c in await _project_clips(project_id):
        if str(c.id) not in clip_ids:
            continue
        cached = (c.ingestion_metadata or {}).get(key)
        if cached is None:
            path = local_storage._full(c.s3_key)
            cached = await asyncio.to_thread(silence_mod.detect_silences, path, noise_db, min_ms) if path.exists() else []
            await _clip_meta_update(c.id, **{key: cached})
        out[str(c.id)] = [tuple(x) for x in cached]
    return out


async def plan_project_cleanup(project_id: uuid.UUID, opts: CleanupOptions):
    rows = await _timeline_rows(project_id)
    clip_ids = sorted({r["clip_id"] for r in rows})
    sil = await silences_by_clip(project_id, clip_ids) if opts.remove_silence else {}
    words = await words_by_clip(project_id)
    return rows, plan_cleanup(rows, sil, words, opts)


async def apply_rows(project_id: uuid.UUID, rows: List[dict], version_label: str) -> None:
    """Replace the timeline, auto-saving the current cut as a version first."""
    async with AsyncSessionLocal() as s:
        repo = StoryTimelineRepository(s)
        current = repo.snapshot(await repo.get_ordered(project_id))
        if current:
            await TimelineVersionRepository(s).create(project_id, label=version_label, snapshot=current)
        await repo.replace_all(project_id, rows)


# ── beats ─────────────────────────────────────────────────────────────────────

async def ensure_beats(project_id: uuid.UUID, track_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as s:
        t = await AudioTrackRepository(s).get(track_id)
        if not t or t.project_id != project_id:
            raise ValueError("Audio track not found")
        if t.analysis and t.analysis.get("version") == 1 and t.analysis.get("beats_ms") is not None:
            return t.analysis
        path = local_storage._full(t.s3_key)
    analysis = await asyncio.to_thread(beats_mod.analyze, path)
    async with AsyncSessionLocal() as s:
        await AudioTrackRepository(s).update(track_id, analysis=analysis)
    return analysis


async def beat_grid(project_id: uuid.UUID, track_id: uuid.UUID, every: int = 1) -> List[int]:
    analysis = await ensure_beats(project_id, track_id)
    rows = await _timeline_rows(project_id)
    async with AsyncSessionLocal() as s:
        t = await AudioTrackRepository(s).get(track_id)
    return beats_mod.beat_grid(analysis, t.start_ms, t.source_offset_ms, t.length_ms, t.loop,
                               int(total_ms(place(rows))), every)


async def plan_project_beat_sync(project_id: uuid.UUID, track_id: uuid.UUID, every: Optional[int] = None):
    rows = await _timeline_rows(project_id)
    if len(rows) < 2:
        raise ValueError("Beat sync needs at least two clips on the timeline")
    analysis = await ensure_beats(project_id, track_id)
    if not analysis.get("beats_ms"):
        raise ValueError("No clear beat found in that track")
    base = await beat_grid(project_id, track_id, 1)
    k = every or auto_every(base, rows)
    # generous horizon: the synced edit may run longer than the current one
    async with AsyncSessionLocal() as s:
        t = await AudioTrackRepository(s).get(track_id)
    grid = beats_mod.beat_grid(analysis, t.start_ms, t.source_offset_ms, t.length_ms, t.loop,
                               int(total_ms(place(rows)) * 3 + 60_000), k)
    return rows, plan_beat_sync(rows, grid, every=k), analysis


# ── reframe ───────────────────────────────────────────────────────────────────

async def subject_tracks(project_id: uuid.UUID, clip_ids: Optional[List[str]] = None) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for c in await _project_clips(project_id):
        if clip_ids is not None and str(c.id) not in clip_ids:
            continue
        tr = (c.ingestion_metadata or {}).get("subject_track")
        if not tr or tr.get("version") != TRACK_VERSION:
            path = local_storage._full(c.s3_key)
            if not path.exists():
                continue
            tr = await asyncio.to_thread(track_subject, path)
            await _clip_meta_update(c.id, subject_track=tr)
        out[str(c.id)] = tr
    return out


async def crops_for_format(project_id: uuid.UUID, segments: List[dict], width: int, height: int) -> List[dict]:
    """Decorate render segments with a per-part crop for this output size (smart reframing)."""
    mode = (await get_settings(project_id))["reframe"]["mode"]
    needs = []
    for sg in segments:
        w, h = sg.get("src_w"), sg.get("src_h")
        if w and h and abs(w / h - width / height) / (width / height) >= 0.03:
            needs.append(sg)
    if not needs:
        return segments
    want_track = {sg["clip_id"] for sg in needs
                  if ((sg.get("reframe_params") or {}).get("mode") or mode) == "smart"}
    tracks = await subject_tracks(project_id, list(want_track)) if want_track else {}
    out = []
    for sg in segments:
        rp = sg.get("reframe_params") or {}
        m = rp.get("mode") or mode
        manual = rp.get("x") if m == "manual" else None
        crop = crop_filter(sg.get("src_w") or 0, sg.get("src_h") or 0, width, height,
                           "center" if m == "manual" else m, tracks.get(sg.get("clip_id")),
                           sg["start_ms"], sg["end_ms"], manual)
        out.append({**sg, "crop": crop})
    return out
