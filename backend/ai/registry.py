"""Single place that decides which provider implementation backs each capability."""
from backend.ai import providers as p
from backend.ai.base import (
    CommandInterpreterProvider, EditDecisionProvider, StoryProvider, TranscriptionProvider, VisionProvider,
)
from backend.config import settings

_TRANSCRIPTION = {
    "placeholder": p.PlaceholderTranscriptionProvider,
    "local": p.LocalTranscriptionProvider,
    "cloud": p.CloudTranscriptionProvider,
}
_VISION = {
    "placeholder": p.PlaceholderVisionProvider,
    "local": p.LocalVisionProvider,
    "cloud": p.CloudVisionProvider,
}
_STORY = {
    "placeholder": p.PlaceholderStoryProvider,
    "local": p.LocalStoryProvider,
    "cloud": p.CloudStoryProvider,
}
_EDIT_DECISION = {
    "placeholder": p.PlaceholderEditDecisionProvider,
    "local": p.LocalEditDecisionProvider,
    "cloud": p.CloudEditDecisionProvider,
}


def get_transcription_provider() -> TranscriptionProvider:
    return _TRANSCRIPTION[settings.AI_MODE]()


def get_vision_provider() -> VisionProvider:
    return _VISION[settings.AI_MODE]()


def get_story_provider() -> StoryProvider:
    return _STORY[settings.AI_MODE]()


def get_edit_decision_provider() -> EditDecisionProvider:
    return _EDIT_DECISION[settings.AI_MODE]()


def get_interpreter_provider() -> CommandInterpreterProvider:
    # The rule-based interpreter needs no model, so it stays the default
    # even outside placeholder mode until a real NLU provider is implemented.
    return p.RuleBasedInterpreter()
