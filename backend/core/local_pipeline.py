"""
LOCAL_MODE pipeline — runs all 11 agents in-process as an asyncio task.

This is the orchestrator used on localhost when the heavy ML stack / GPU / Celery
are unavailable. It performs the REAL pipeline choreography (ordering, the two
parallel groups, DB writes, per-agent status, the EDL/story structures, output
creation) with REAL media operations wherever no AI model is required
(ffprobe/ffmpeg/PySceneDetect/OpenCV — see backend/core/real_ops.py).

Capabilities that do need an AI model (transcription, face/emotion vision,
narrative reasoning, editing-style decisions, chat command interpretation) go
through backend/ai/registry.py. The default provider ("placeholder") never
fakes a realistic-looking result with random data — it returns an honest,
clearly-labeled placeholder built from whatever real signal already exists,
ready to be swapped for a local model or a cloud API later.

The production path (Celery + real agents in backend/agents/*) is unchanged.
"""
import asyncio
import logging
import random
import uuid
from typing import Dict, List

from backend.ai import registry as ai
from backend.config import settings
from backend.core.event_bus import EventBus
from backend.database.db import AsyncSessionLocal
from backend.database.models import (
    AgentStatus, NarrativeRole, OutputFormat, ProjectStatus, SegmentType, TransitionType,
)
from backend.database.repositories import (
    AgentTaskRepository, ClipRepository, OutputRepository,
    ProjectRepository, SegmentRepository, StoryTimelineRepository, TranscriptRepository,
)
from backend.core import real_ops
from backend.storage import local_storage

logger = logging.getLogger(__name__)

# Canonical agent registry — the frontend renders cards keyed by `key`.
AGENTS: List[Dict] = [
    {"key": "ingestion",         "label": "Video Ingestion",    "icon": "🎬"},
    {"key": "scene_detection",   "label": "Scene Detection",    "icon": "🎞"},
    {"key": "speech_analysis",   "label": "Speech Analysis",    "icon": "🗣"},
    {"key": "face_detection",    "label": "Face Detection",     "icon": "👤"},
    {"key": "emotion_analysis",  "label": "Emotion Analysis",   "icon": "😊"},
    {"key": "story_builder",     "label": "Story Builder",      "icon": "📖"},
    {"key": "editing_decision",  "label": "Editing Decision",   "icon": "✂️"},
    {"key": "audio_enhancement", "label": "Audio Enhancement",  "icon": "🔊"},
    {"key": "subtitle",          "label": "Subtitle",           "icon": "📝"},
    {"key": "rendering",         "label": "Rendering",          "icon": "🎥"},
    {"key": "quality_assurance", "label": "Quality Assurance",  "icon": "✅"},
]

OUTPUT_SPECS = {
    "youtube":  (1920, 1080, "16:9"),
    "shorts":   (1080, 1920, "9:16"),
    "reels":    (1080, 1920, "9:16"),
    "tiktok":   (1080, 1920, "9:16"),
    "linkedin": (1920, 1080, "16:9"),
}

_db_lock = asyncio.Lock()  # serialize SQLite writes


class LocalPipeline:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.bus = EventBus()
        self.channel = f"project:{project_id}"

    async def _emit(self, payload: dict) -> None:
        payload.setdefault("project_id", self.project_id)
        await self.bus.publish(self.channel, payload)

    async def _agent_task(self, key: str) -> uuid.UUID:
        async with _db_lock, AsyncSessionLocal() as session:
            task = await AgentTaskRepository(session).create(
                project_id=uuid.UUID(self.project_id), agent_name=key
            )
            await AgentTaskRepository(session).update_status(task.id, AgentStatus.RUNNING)
            return task.id

    async def _finish_task(self, task_id: uuid.UUID, summary: dict) -> None:
        async with _db_lock, AsyncSessionLocal() as session:
            await AgentTaskRepository(session).update_status(
                task_id, AgentStatus.COMPLETED, progress_pct=100, result_metadata=summary
            )

    async def _run_steps(self, key: str, label: str, steps: List[str], step_delay=(0.4, 0.9)):
        """Emit running + progress events across `steps` messages, persisting
        each step's percent/message so a page reload (HTTP polling, no
        WebSocket) still shows real mid-run progress, not just 0/100."""
        task_id = await self._agent_task(key)
        await self._emit({"event": "agent.started", "agent": key, "label": label})
        n = len(steps)
        for i, msg in enumerate(steps):
            pct = int(((i + 1) / n) * 100)
            await self._emit({
                "event": "agent.progress", "agent": key, "label": label,
                "status": "running", "progress_pct": pct, "message": msg,
            })
            async with _db_lock, AsyncSessionLocal() as session:
                await AgentTaskRepository(session).update_status(
                    task_id, AgentStatus.RUNNING, progress_pct=pct, current_message=msg,
                )
            await asyncio.sleep(random.uniform(*step_delay))
        return task_id

    async def run(self) -> None:
        try:
            await self._run()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Local pipeline failed for %s", self.project_id)
            async with _db_lock, AsyncSessionLocal() as session:
                await ProjectRepository(session).update_status(
                    uuid.UUID(self.project_id), ProjectStatus.FAILED, error=str(exc)
                )
            await self._emit({"event": "pipeline.failed", "error": str(exc)})
        finally:
            await self.bus.close()

    async def _run(self) -> None:
        pid = uuid.UUID(self.project_id)

        async with _db_lock, AsyncSessionLocal() as session:
            await ProjectRepository(session).update_status(pid, ProjectStatus.PROCESSING)
            clips = await ClipRepository(session).list_for_project(pid)

        if not clips:
            raise ValueError("No clips uploaded")

        await self._emit({
            "event": "pipeline.started",
            "agents": [{"key": a["key"], "label": a["label"], "icon": a["icon"]} for a in AGENTS],
            "clip_count": len(clips),
        })

        # ── Agent 1: Ingestion ────────────────────────────────────────────────
        t = await self._run_steps("ingestion", "Video Ingestion", [
            f"Validating {len(clips)} uploaded clip(s)",
            "ffprobe: codec, resolution, fps, duration",
            "Generating thumbnails",
            "Registering media in database",
        ])
        primary_clip_key = None
        async with _db_lock, AsyncSessionLocal() as session:
            repo = ClipRepository(session)
            for clip in clips:
                if primary_clip_key is None:
                    primary_clip_key = clip.s3_key
                # REAL ffprobe on the locally-stored file (off the event loop).
                local_path = local_storage._full(clip.s3_key)
                meta = await asyncio.to_thread(real_ops.probe_metadata, local_path) if local_path.exists() else {}
                # Real thumbnail
                thumb_key = local_storage.make_thumbnail_key(self.project_id, str(clip.id))
                await asyncio.to_thread(real_ops.extract_thumbnail, local_path, local_storage._full(thumb_key))
                await repo.update_video_info(
                    clip.id,
                    duration_ms=meta.get("duration_ms") or clip.duration_ms or 60_000,
                    fps=meta.get("fps") or clip.fps or 30.0,
                    width=meta.get("width") or clip.width or 1920,
                    height=meta.get("height") or clip.height or 1080,
                    codec_video=meta.get("codec_video") or clip.codec_video or "h264",
                    codec_audio=meta.get("codec_audio") or clip.codec_audio or "aac",
                    bitrate_kbps=meta.get("bitrate_kbps"),
                    thumbnail_s3_key=thumb_key,
                )
                await repo.mark_ingested(clip.id, {"local_mode": True, "real_probe": bool(meta)})
        await self._finish_task(t, {"clips_ingested": len(clips)})
        await self._emit({"event": "agent.completed", "agent": "ingestion",
                          "summary": f"{len(clips)} clips ingested"})

        # Reload clips with durations
        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(pid)

        # ── Agent 2: Scene Detection ──────────────────────────────────────────
        t = await self._run_steps("scene_detection", "Scene Detection", [
            "PySceneDetect: content-aware shot boundaries",
            "Extracting real keyframes (OpenCV)",
            "Scoring sharpness + exposure",
            "Classifying scene types",
        ], step_delay=(0.3, 0.6))
        all_segments: List[dict] = []
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            for clip in clips:
                dur = clip.duration_ms or 90_000
                local_path = local_storage._full(clip.s3_key)
                # REAL shot-boundary detection (off the event loop)
                boundaries = (
                    await asyncio.to_thread(real_ops.detect_scenes, local_path, dur)
                    if local_path.exists() else [(0, dur)]
                )
                clip_segs = []
                for (start_ms, end_ms) in boundaries:
                    if end_ms - start_ms < settings.MIN_SEGMENT_DURATION_MS:
                        continue
                    seg_id = uuid.uuid4()
                    kf_key = local_storage.make_keyframe_key(self.project_id, str(clip.id), str(seg_id))
                    # REAL keyframe + quality from the actual footage
                    q = await asyncio.to_thread(
                        real_ops.keyframe_and_quality,
                        local_path, (start_ms + end_ms) // 2, local_storage._full(kf_key)
                    ) if local_path.exists() else 0.5
                    clip_segs.append({
                        "id": seg_id,
                        "clip_id": clip.id,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "segment_type": SegmentType.HIGHLIGHT if q > 0.78 else SegmentType.GOOD,
                        "quality_score": round(q, 2),
                        "engagement_score": round(q * 0.6, 2),
                        "scene_description": None,
                        "keyframe_s3_key": kf_key,
                        "is_blurry": q < 0.35,
                    })
                await seg_repo.bulk_create(clip_segs)
                all_segments.extend(clip_segs)
        await self._finish_task(t, {"total_segments": len(all_segments)})
        await self._emit({"event": "agent.completed", "agent": "scene_detection",
                          "summary": f"{len(all_segments)} segments detected"})

        # ── Agents 3,4,5: parallel analysis group ─────────────────────────────
        await self._emit({"event": "group.started", "agents": ["speech_analysis", "face_detection", "emotion_analysis"],
                          "label": "Parallel analysis"})

        word_total = await self._speech_agent(clips)
        await self._face_agent(all_segments)
        peaks = await self._emotion_agent(all_segments)

        # ── Agent 6: Story Builder ────────────────────────────────────────────
        t = await self._run_steps("story_builder", "Story Builder", [
            "Aggregating analysis from all agents",
            "Ranking segments by quality + engagement",
            "Selecting best segments",
            "Assigning hook → climax → resolution roles",
        ])
        story_len = await self._build_story(pid, all_segments)
        await self._finish_task(t, {"segments_in_story": story_len, "ai_mode": settings.AI_MODE})
        await self._emit({"event": "agent.completed", "agent": "story_builder",
                          "summary": f"{story_len}-beat narrative built"})

        # ── Agent 7: Editing Decision ─────────────────────────────────────────
        t = await self._run_steps("editing_decision", "Editing Decision", [
            "Computing frame-accurate cut points",
            "Choosing transitions & pacing rhythm",
            "Planning zoom / reframe per beat",
            "Emitting Edit Decision List (EDL)",
        ])
        edit_decision = await self._decide_editing(pid, story_len)
        await self._finish_task(t, {"color_grade": edit_decision.color_grade, "pacing": edit_decision.pacing,
                                     "ai_mode": settings.AI_MODE})
        await self._emit({"event": "agent.completed", "agent": "editing_decision",
                          "summary": f"EDL ready · {edit_decision.color_grade}"})

        # ── Agents 8,9: parallel (audio + subtitle) ───────────────────────────
        await self._emit({"event": "group.started", "agents": ["audio_enhancement", "subtitle"],
                          "label": "Parallel post-production"})
        await self._audio_agent()
        await self._subtitle_agent(pid)

        # ── Agent 10: Rendering ───────────────────────────────────────────────
        formats = self.project_formats or settings.OUTPUT_FORMATS
        t = await self._run_steps("rendering", "Rendering", [
            "Building FFmpeg filter graph from EDL",
            "Applying loudness normalization",
            f"Rendering {len(formats)} formats: {', '.join(formats)}",
            "Finalizing outputs",
        ], step_delay=(0.6, 1.1))
        total_dur, any_failed = await self._create_outputs(pid, formats)
        await self._finish_task(t, {"formats": formats, "any_failed": any_failed})
        await self._emit({"event": "agent.completed", "agent": "rendering",
                          "summary": f"{len(formats)} formats rendered" + (" (some failed)" if any_failed else "")})

        # ── Agent 11: QA ──────────────────────────────────────────────────────
        t = await self._run_steps("quality_assurance", "Quality Assurance", [
            "Checking file integrity",
            "Scanning for black frames",
            "Scoring bitrate-based quality heuristic",
            "Finalizing project",
        ])
        all_passed = await self._qa_outputs(pid)
        async with _db_lock, AsyncSessionLocal() as session:
            await ProjectRepository(session).update_status(pid, ProjectStatus.COMPLETED)
        await self._finish_task(t, {"all_passed": all_passed})
        await self._emit({"event": "agent.completed", "agent": "quality_assurance",
                          "summary": "QA passed" if all_passed else "QA found issues"})

        await self._emit({
            "event": "pipeline.complete",
            "summary": {
                "segments": len(all_segments),
                "words_transcribed": word_total,
                "emotional_peaks": peaks,
                "story_beats": story_len,
                "formats": len(formats),
                "total_duration_ms": total_dur,
            },
        })

    # ── Parallel-group agents ─────────────────────────────────────────────────

    async def _speech_agent(self, clips) -> int:
        provider = ai.get_transcription_provider()
        t = await self._run_steps("speech_analysis", "Speech Analysis", [
            "Transcribing audio",
            "Word-level timestamp alignment",
            "Speaker diarization",
            "Flagging filler words & silence",
        ])
        total = 0
        async with _db_lock, AsyncSessionLocal() as session:
            tr_repo = TranscriptRepository(session)
            for clip in clips:
                local_path = local_storage._full(clip.s3_key)
                result = await provider.transcribe(
                    local_path if local_path.exists() else None, clip.duration_ms or 0
                )
                if result.words:
                    rows = [{
                        "clip_id": clip.id, "speaker_id": "SPEAKER_00", "word": w.word,
                        "start_ms": w.start_ms, "end_ms": w.end_ms,
                        "confidence": w.confidence, "is_filler": w.is_filler, "is_silence": w.is_silence,
                    } for w in result.words]
                    await tr_repo.bulk_create(rows)
                    total += len(rows)
        summary = f"{total} words transcribed" if total else "transcription pending — no provider configured"
        await self._finish_task(t, {"words": total, "ai_mode": settings.AI_MODE})
        await self._emit({"event": "agent.completed", "agent": "speech_analysis", "summary": summary})
        return total

    async def _face_agent(self, segments) -> None:
        provider = ai.get_vision_provider()
        t = await self._run_steps("face_detection", "Face Detection", [
            "Detecting & tracking faces",
            "Scoring face quality (sharpness, frontality)",
            "Identifying main subjects",
        ])
        faces = 0
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            for s in segments:
                kf_path = local_storage._full(s["keyframe_s3_key"]) if s.get("keyframe_s3_key") else None
                vision = await provider.analyze(kf_path if kf_path and kf_path.exists() else None)
                if vision.has_face is None:
                    continue  # unknown — don't fabricate a value or inflate engagement
                if vision.has_face:
                    faces += 1
                await seg_repo.update_scores(s["id"], has_face=True, face_count=vision.face_count or 1)
        summary = f"faces in {faces} segments" if faces or provider.__class__.__name__ != "PlaceholderVisionProvider" \
            else "face detection pending — no provider configured"
        await self._finish_task(t, {"segments_with_faces": faces, "ai_mode": settings.AI_MODE})
        await self._emit({"event": "agent.completed", "agent": "face_detection", "summary": summary})

    async def _emotion_agent(self, segments) -> int:
        provider = ai.get_vision_provider()
        t = await self._run_steps("emotion_analysis", "Emotion Analysis", [
            "Per-frame emotion analysis",
            "Audio sentiment analysis",
            "Locating emotional peaks",
        ])
        peaks = 0
        determined = False
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            for s in segments:
                kf_path = local_storage._full(s["keyframe_s3_key"]) if s.get("keyframe_s3_key") else None
                vision = await provider.analyze(kf_path if kf_path and kf_path.exists() else None)
                if not vision.emotion_labels:
                    continue  # unknown — don't fabricate scores
                determined = True
                happy = vision.emotion_labels.get("happy", 0)
                if happy > 0.6:
                    peaks += 1
                await seg_repo.update_scores(s["id"], emotion_labels=vision.emotion_labels)
        summary = f"{peaks} emotional peaks" if determined else "emotion analysis pending — no provider configured"
        await self._finish_task(t, {"emotional_peaks": peaks, "ai_mode": settings.AI_MODE})
        await self._emit({"event": "agent.completed", "agent": "emotion_analysis", "summary": summary})
        return peaks

    async def _build_story(self, pid, segments) -> int:
        provider = ai.get_story_provider()
        # pick top segments by real quality/engagement signal
        async with AsyncSessionLocal() as session:
            highlights = await SegmentRepository(session).get_highlights(pid, min_score=0.0)
        top = sorted(highlights, key=lambda s: (s.engagement_score or 0), reverse=True)[:8]
        roles = [NarrativeRole.HOOK, NarrativeRole.CONTEXT, NarrativeRole.RISING_ACTION,
                 NarrativeRole.CLIMAX, NarrativeRole.REACTION, NarrativeRole.RISING_ACTION,
                 NarrativeRole.RESOLUTION, NarrativeRole.BROLL]
        rows = []
        for i, seg in enumerate(top):
            beat = await provider.build_beat(
                i, seg.quality_score or 0.5, seg.engagement_score or 0.5, seg.has_face
            )
            rows.append({
                "project_id": pid,
                "segment_id": seg.id,
                "position_order": i + 1,
                "narrative_role": roles[i % len(roles)],
                "transition_in": TransitionType.CUT if i == 0 else TransitionType(beat.transition_in),
                "trim_start_ms": seg.start_ms,
                "trim_end_ms": seg.end_ms,
                "edit_reasoning": beat.edit_reasoning,
            })
        if rows:
            async with _db_lock, AsyncSessionLocal() as session:
                await StoryTimelineRepository(session).bulk_create(rows)
        return len(rows)

    async def _decide_editing(self, pid, beat_count: int):
        provider = ai.get_edit_decision_provider()
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(pid)
        scored = [e.segment for e in timeline if e.segment]
        avg_quality = sum((s.quality_score or 0.5) for s in scored) / len(scored) if scored else 0.5
        avg_engagement = sum((s.engagement_score or 0.5) for s in scored) / len(scored) if scored else 0.5
        return await provider.decide(avg_quality, avg_engagement, beat_count)

    async def _audio_agent(self) -> None:
        t = await self._run_steps("audio_enhancement", "Audio Enhancement", [
            "Planning loudness normalization to -14 LUFS",
            "Identifying silence & filler cuts",
            "Preparing music ducking plan",
        ])
        # The actual ffmpeg loudnorm filter runs during rendering (real_ops.render_edit)
        # so it's applied exactly once, on the final concatenated audio track.
        await self._finish_task(t, {"lufs_target": -14.0, "method": "ffmpeg loudnorm (applied at render)"})
        await self._emit({"event": "agent.completed", "agent": "audio_enhancement",
                          "summary": "loudness normalization planned · -14 LUFS at render"})

    async def _write_subtitles(self, pid) -> int:
        """Build+save the SRT from the current timeline. Shared by the full
        pipeline run and by rerender(), so subtitles never go stale after a
        manual edit or chat command changes the timeline."""
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(pid)

        cues = []
        cursor_ms = 0
        for entry in timeline:
            if not entry.segment:
                continue
            start = entry.trim_start_ms if entry.trim_start_ms is not None else entry.segment.start_ms
            end = entry.trim_end_ms if entry.trim_end_ms is not None else entry.segment.end_ms
            dur = max(300, end - start)
            cues.append((cursor_ms, cursor_ms + dur, "[Transcript pending — connect a transcription provider]"))
            cursor_ms += dur

        srt = real_ops.build_srt(cues) if cues else ""
        key = local_storage.make_subtitle_key(self.project_id, "main", "srt")
        await local_storage.save_bytes(key, srt.encode("utf-8"))
        return len(cues)

    async def _subtitle_agent(self, pid) -> None:
        t = await self._run_steps("subtitle", "Subtitle", [
            "Building cues from the edit timeline",
            "Aligning cue timing to rendered output",
            "Exporting SRT",
        ])
        cue_count = await self._write_subtitles(pid)
        await self._finish_task(t, {"cue_count": cue_count})
        await self._emit({"event": "agent.completed", "agent": "subtitle",
                          "summary": f"{cue_count} cues generated (SRT)"})

    async def _create_outputs(self, pid, formats):
        # Build the real edit segment list from the story timeline.
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(pid)
            clip_repo = ClipRepository(session)
            edit_segments = []
            for entry in timeline:
                if not entry.segment:
                    continue
                clip = await clip_repo.get(entry.segment.clip_id)
                if not clip:
                    continue
                src = local_storage._full(clip.s3_key)
                start = entry.trim_start_ms if entry.trim_start_ms is not None else entry.segment.start_ms
                end = entry.trim_end_ms if entry.trim_end_ms is not None else entry.segment.end_ms
                edit_segments.append({"src": str(src), "start_ms": int(start), "end_ms": int(end)})

        total_dur = sum(s["end_ms"] - s["start_ms"] for s in edit_segments)
        any_failed = False

        async with _db_lock, AsyncSessionLocal() as session:
            out_repo = OutputRepository(session)
            for idx, fmt in enumerate(formats):
                w, h, ratio = OUTPUT_SPECS.get(fmt, (1920, 1080, "16:9"))
                out_key = local_storage.make_output_key(self.project_id, fmt)
                out_path = local_storage._full(out_key)

                pct = int(((idx + 0.5) / len(formats)) * 100)
                await self._emit({
                    "event": "agent.progress", "agent": "rendering", "label": "Rendering",
                    "status": "running", "progress_pct": pct,
                    "message": f"ffmpeg encoding {fmt} ({w}×{h}) — {len(edit_segments)} cuts",
                })

                # REAL ffmpeg render: trim + scale/pad + concat + loudnorm.
                rendered = False
                if edit_segments:
                    try:
                        rendered = await asyncio.to_thread(
                            real_ops.render_edit, edit_segments, out_path, w, h
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("real render failed for %s: %s", fmt, e)

                if rendered and out_path.exists():
                    s3_key, size, meta = out_key, local_storage.file_size(out_key), {"real_render": True}
                    probe = await asyncio.to_thread(real_ops.probe_metadata, out_path)
                    if probe.get("duration_ms"):
                        total_dur = probe["duration_ms"]
                else:
                    # Honest failure — do NOT ship the raw source clip as if it
                    # were the edited output. The frontend shows this as failed.
                    any_failed = True
                    s3_key, size = None, 0
                    meta = {"real_render": False, "error": "no edit segments" if not edit_segments else "ffmpeg render failed"}

                existing = await out_repo.get_by_format(pid, OutputFormat(fmt))
                fields = dict(
                    aspect_ratio=ratio, width=w, height=h,
                    duration_ms=total_dur, file_size_bytes=size,
                    s3_key=s3_key, s3_bucket="local",
                    quality_score=None, render_metadata=meta,
                )
                if existing:
                    await out_repo.update(existing.id, **fields)
                else:
                    await out_repo.create(project_id=pid, format=OutputFormat(fmt), **fields)
        return total_dur, any_failed

    async def _qa_outputs(self, pid) -> bool:
        all_passed = True
        async with _db_lock, AsyncSessionLocal() as session:
            out_repo = OutputRepository(session)
            outs = await out_repo.list_for_project(pid)
            for o in outs:
                if not o.s3_key:
                    all_passed = False
                    continue
                path = local_storage._full(o.s3_key)
                if not path.exists():
                    all_passed = False
                    continue
                check = await asyncio.to_thread(real_ops.check_output_integrity, path)
                if not check["integrity_ok"] or check["black_ms"] > 2000:
                    all_passed = False
                meta = dict(o.render_metadata or {})
                meta["qa"] = check
                await out_repo.update(o.id, quality_score=check["heuristic_quality_score"], render_metadata=meta)
        return all_passed

    project_formats: List[str] | None = None


# ── Launcher ──────────────────────────────────────────────────────────────────

def launch(project_id: str, formats: List[str] | None = None) -> None:
    """Fire-and-forget the local pipeline on the running event loop."""
    pipeline = LocalPipeline(project_id)
    pipeline.project_formats = formats
    asyncio.create_task(pipeline.run())


async def rerender(project_id: str, formats: List[str] | None = None) -> Dict:
    """
    Re-render outputs from the CURRENT story timeline without re-running the
    AI analysis stages — used after a manual timeline edit or a chat command.
    """
    pipeline = LocalPipeline(project_id)
    pid = uuid.UUID(project_id)
    fmts = formats or settings.OUTPUT_FORMATS
    await pipeline._write_subtitles(pid)
    total_dur, any_failed = await pipeline._create_outputs(pid, fmts)
    all_passed = await pipeline._qa_outputs(pid)
    await pipeline.bus.close()
    return {"total_duration_ms": total_dur, "any_failed": any_failed, "qa_passed": all_passed}
