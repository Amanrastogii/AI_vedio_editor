"""
Concrete providers for each AI capability.

Placeholder* — the default (`AI_MODE=placeholder`). Never calls a model or an
API. Output is honest about being unconfigured: no invented transcript text,
no random face/emotion guesses. Where a real, cheap signal already exists
(quality/engagement scores from backend/core/real_ops.py) it's used instead of
being ignored.

Local*/Cloud* — intentionally unimplemented. Selecting AI_MODE=local or
AI_MODE=cloud without filling these in raises a clear error rather than
silently falling back, so a half-wired provider is never mistaken for a
working one.
"""
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from backend.ai.base import (
    CommandInterpreterProvider, EditDecision, EditDecisionProvider, InterpretResult,
    StoryBeat, StoryProvider, TranscriptionProvider, TranscriptionResult, VisionProvider, VisionResult,
)

logger = logging.getLogger(__name__)

NOT_WIRED = "AI_MODE={mode!r} but no {capability} provider is implemented yet — set AI_MODE=placeholder, or implement backend/ai/providers.py's {cls} first."


# ── Transcription ────────────────────────────────────────────────────────────

class PlaceholderTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, audio_path: Optional[Path], duration_ms: int) -> TranscriptionResult:
        return TranscriptionResult(words=[], provider="placeholder")


class LocalTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, audio_path: Optional[Path], duration_ms: int) -> TranscriptionResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="local", capability="transcription", cls="LocalTranscriptionProvider"))


class CloudTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, audio_path: Optional[Path], duration_ms: int) -> TranscriptionResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="cloud", capability="transcription", cls="CloudTranscriptionProvider"))


# ── Vision (face + emotion) ───────────────────────────────────────────────────

class PlaceholderVisionProvider(VisionProvider):
    async def analyze(self, keyframe_path: Optional[Path]) -> VisionResult:
        return VisionResult(has_face=None, face_count=None, emotion_labels=None, provider="placeholder")


class LocalVisionProvider(VisionProvider):
    async def analyze(self, keyframe_path: Optional[Path]) -> VisionResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="local", capability="vision", cls="LocalVisionProvider"))


class CloudVisionProvider(VisionProvider):
    async def analyze(self, keyframe_path: Optional[Path]) -> VisionResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="cloud", capability="vision", cls="CloudVisionProvider"))


# ── Story building ────────────────────────────────────────────────────────────

class PlaceholderStoryProvider(StoryProvider):
    """Reasoning is generated from real upstream scores — not invented, just
    not yet narrated by an LLM."""

    async def build_beat(self, index: int, quality_score: float, engagement_score: float,
                          has_face: Optional[bool]) -> StoryBeat:
        face_note = " · face detected" if has_face else ("" if has_face is None else " · no face")
        reasoning = (
            f"Selected by quality/engagement score (quality {quality_score:.2f}, "
            f"engagement {engagement_score:.2f}{face_note}) — connect a story provider "
            f"for narrative-driven reasoning."
        )
        return StoryBeat(edit_reasoning=reasoning, transition_in="cut")


class LocalStoryProvider(StoryProvider):
    async def build_beat(self, index, quality_score, engagement_score, has_face) -> StoryBeat:
        raise NotImplementedError(
            NOT_WIRED.format(mode="local", capability="story building", cls="LocalStoryProvider"))


class CloudStoryProvider(StoryProvider):
    async def build_beat(self, index, quality_score, engagement_score, has_face) -> StoryBeat:
        raise NotImplementedError(
            NOT_WIRED.format(mode="cloud", capability="story building", cls="CloudStoryProvider"))


# ── Editing decision ──────────────────────────────────────────────────────────

class PlaceholderEditDecisionProvider(EditDecisionProvider):
    """Pacing/color-grade derived from real aggregate stats, not hardcoded."""

    async def decide(self, avg_quality: float, avg_engagement: float, beat_count: int) -> EditDecision:
        pacing = "dynamic" if avg_engagement >= 0.6 or beat_count >= 6 else "steady"
        color_grade = "natural" if avg_quality < 0.6 else "clean"
        return EditDecision(color_grade=color_grade, pacing=pacing, provider="placeholder")


class LocalEditDecisionProvider(EditDecisionProvider):
    async def decide(self, avg_quality, avg_engagement, beat_count) -> EditDecision:
        raise NotImplementedError(
            NOT_WIRED.format(mode="local", capability="editing decision", cls="LocalEditDecisionProvider"))


class CloudEditDecisionProvider(EditDecisionProvider):
    async def decide(self, avg_quality, avg_engagement, beat_count) -> EditDecision:
        raise NotImplementedError(
            NOT_WIRED.format(mode="cloud", capability="editing decision", cls="CloudEditDecisionProvider"))


# ── Chat command interpreter ──────────────────────────────────────────────────

_TRANSITION_WORDS = {
    "cut": "cut", "dissolve": "dissolve", "fade": "fade_to_black", "fade to black": "fade_to_black",
    "wipe": "wipe", "zoom in": "zoom_in", "zoom out": "zoom_out",
    "cross fade": "cross_fade", "crossfade": "cross_fade", "cross-fade": "cross_fade",
}


class RuleBasedInterpreter(CommandInterpreterProvider):
    """
    Fully functional today — no model required. Understands a small set of
    explicit timeline commands and applies them directly via
    StoryTimelineRepository. This is intentionally the default even in
    AI_MODE=local/cloud until a real NLU provider is implemented — swap it out
    in registry.py once one exists.
    """

    HELP = (
        "I understand: \"remove clip 2\", \"move clip 3 to position 1\", "
        "\"trim clip 2 start to 5s\", \"trim clip 2 end to 12s\", "
        "\"change transition of clip 2 to fade\"."
    )

    async def interpret(self, message: str, project_id: str) -> InterpretResult:
        from backend.database.db import AsyncSessionLocal
        from backend.database.models import TransitionType
        from backend.database.repositories import StoryTimelineRepository

        text = message.strip().lower()
        pid = uuid.UUID(project_id)

        async with AsyncSessionLocal() as session:
            repo = StoryTimelineRepository(session)
            timeline = await repo.get_ordered(pid)
            if not timeline:
                return InterpretResult(reply="The timeline is empty — nothing to edit yet.")

            m = re.search(r"(?:remove|delete)\s+clip\s+(\d+)", text)
            if m:
                pos = int(m.group(1))
                entry = await repo.get_by_position(pid, pos)
                if not entry:
                    return InterpretResult(reply=f"There's no clip at position {pos}.")
                await repo.delete_entry(entry.id)
                remaining = await repo.get_ordered(pid)
                await repo.reorder(pid, [e.id for e in remaining])
                return InterpretResult(
                    reply=f"Removed clip {pos} from the timeline.",
                    action={"type": "remove", "position": pos},
                )

            m = re.search(r"move\s+clip\s+(\d+)\s+to(?:\s+position)?\s+(\d+)", text)
            if m:
                src, dst = int(m.group(1)), int(m.group(2))
                entry = await repo.get_by_position(pid, src)
                if not entry:
                    return InterpretResult(reply=f"There's no clip at position {src}.")
                ids = [e.id for e in timeline]
                ids.remove(entry.id)
                dst_idx = max(0, min(len(ids), dst - 1))
                ids.insert(dst_idx, entry.id)
                await repo.reorder(pid, ids)
                return InterpretResult(
                    reply=f"Moved clip {src} to position {dst}.",
                    action={"type": "move", "from": src, "to": dst},
                )

            m = re.search(r"trim\s+clip\s+(\d+)\s+(start|end)\s+to\s+([\d.]+)\s*s", text)
            if m:
                pos, edge, secs = int(m.group(1)), m.group(2), float(m.group(3))
                entry = await repo.get_by_position(pid, pos)
                if not entry or not entry.segment:
                    return InterpretResult(reply=f"There's no clip at position {pos}.")
                seg = entry.segment
                target_ms = seg.start_ms + int(secs * 1000)
                target_ms = max(seg.start_ms, min(seg.end_ms, target_ms))
                if edge == "start":
                    await repo.update_entry(entry.id, trim_start_ms=target_ms)
                else:
                    await repo.update_entry(entry.id, trim_end_ms=target_ms)
                return InterpretResult(
                    reply=f"Trimmed clip {pos}'s {edge} to {secs:.1f}s into the segment.",
                    action={"type": "trim", "position": pos, "edge": edge, "seconds": secs},
                )

            m = re.search(r"transition\s+(?:of\s+)?clip\s+(\d+)\s+to\s+([a-z\s-]+)", text)
            if m:
                pos, word = int(m.group(1)), m.group(2).strip()
                value = _TRANSITION_WORDS.get(word)
                if not value:
                    return InterpretResult(
                        reply=f"I don't know the transition \"{word}\". Try: {', '.join(sorted(set(_TRANSITION_WORDS.values())))}.")
                entry = await repo.get_by_position(pid, pos)
                if not entry:
                    return InterpretResult(reply=f"There's no clip at position {pos}.")
                await repo.update_entry(entry.id, transition_in=TransitionType(value))
                return InterpretResult(
                    reply=f"Set clip {pos}'s transition to {value}.",
                    action={"type": "transition", "position": pos, "value": value},
                )

        return InterpretResult(reply=f"I didn't understand that. {self.HELP}")


class LocalInterpreterProvider(CommandInterpreterProvider):
    async def interpret(self, message: str, project_id: str) -> InterpretResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="local", capability="chat interpretation", cls="LocalInterpreterProvider"))


class CloudInterpreterProvider(CommandInterpreterProvider):
    async def interpret(self, message: str, project_id: str) -> InterpretResult:
        raise NotImplementedError(
            NOT_WIRED.format(mode="cloud", capability="chat interpretation", cls="CloudInterpreterProvider"))
