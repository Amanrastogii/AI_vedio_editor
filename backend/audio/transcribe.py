"""
Local speech-to-text with word-level timestamps (faster-whisper / CTranslate2, CPU int8).

Whisper normally "cleans up" speech and drops disfluencies, which would make
filler-word removal impossible; a disfluent initial prompt is the standard
way to make it keep "um", "uh", etc. VAD filtering suppresses the text
Whisper tends to hallucinate over silence or music.
"""
import logging
import re
import threading
from pathlib import Path
from typing import List, Optional

from backend.ai.base import TranscriptionProvider, TranscriptionResult, WordTiming
from backend.config import settings

logger = logging.getLogger(__name__)

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mhm", "eh"}
DISFLUENT_PROMPT = "Umm, let me think like, hmm... Okay, uh, here's what I'm, like, thinking."

_model = None
_model_lock = threading.Lock()


def available() -> bool:
    if settings.TRANSCRIPTION_PROVIDER == "none":
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            logger.info("loading faster-whisper model '%s' (%s/%s) — first use downloads it",
                        settings.WHISPER_MODEL, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)
            _model = WhisperModel(settings.WHISPER_MODEL, device=settings.WHISPER_DEVICE,
                                  compute_type=settings.WHISPER_COMPUTE_TYPE)
        return _model


def normalize(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def is_filler(word: str) -> bool:
    return normalize(word) in FILLERS


def transcribe_words(path: Path) -> List[WordTiming]:
    """Blocking. Returns words with ms timestamps; fillers flagged."""
    model = _get_model()
    segments, _info = model.transcribe(
        str(path),
        word_timestamps=True,
        language=settings.WHISPER_LANGUAGE,
        initial_prompt=DISFLUENT_PROMPT,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
    )
    words: List[WordTiming] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append(WordTiming(
                word=text, start_ms=int(w.start * 1000), end_ms=max(int(w.end * 1000), int(w.start * 1000) + 40),
                confidence=float(w.probability or 0.0), is_filler=is_filler(text),
            ))
    return words


class WhisperTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, audio_path: Optional[Path], duration_ms: int) -> TranscriptionResult:
        import asyncio
        if audio_path is None:
            return TranscriptionResult(words=[], provider="faster-whisper")
        words = await asyncio.to_thread(transcribe_words, audio_path)
        return TranscriptionResult(words=words, provider=f"faster-whisper:{settings.WHISPER_MODEL}")
