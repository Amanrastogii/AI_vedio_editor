"""Interfaces + shared result types for every AI-dependent pipeline capability."""
import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class WordTiming:
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    is_filler: bool = False
    is_silence: bool = False


@dataclass
class TranscriptionResult:
    words: List[WordTiming]
    provider: str


@dataclass
class VisionResult:
    """Per-segment face/emotion read. `None` fields mean "not determined" —
    honest, rather than a random guess dressed up as a detection."""
    has_face: Optional[bool]
    face_count: Optional[int]
    emotion_labels: Optional[Dict[str, float]]
    provider: str


@dataclass
class StoryBeat:
    edit_reasoning: str
    transition_in: str  # TransitionType value


@dataclass
class EditDecision:
    color_grade: str
    pacing: str
    provider: str


@dataclass
class InterpretResult:
    reply: str
    action: Optional[Dict[str, Any]] = None
    provider: str = "rule_based"


class TranscriptionProvider(abc.ABC):
    @abc.abstractmethod
    async def transcribe(self, audio_path: Optional[Path], duration_ms: int) -> TranscriptionResult: ...


class VisionProvider(abc.ABC):
    @abc.abstractmethod
    async def analyze(self, keyframe_path: Optional[Path]) -> VisionResult: ...


class StoryProvider(abc.ABC):
    @abc.abstractmethod
    async def build_beat(self, index: int, quality_score: float, engagement_score: float,
                          has_face: Optional[bool]) -> StoryBeat: ...


class EditDecisionProvider(abc.ABC):
    @abc.abstractmethod
    async def decide(self, avg_quality: float, avg_engagement: float, beat_count: int) -> EditDecision: ...


class CommandInterpreterProvider(abc.ABC):
    @abc.abstractmethod
    async def interpret(self, message: str, project_id: str) -> InterpretResult: ...
