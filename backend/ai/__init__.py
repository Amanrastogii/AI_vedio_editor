"""
AI provider abstraction.

Every capability that eventually needs a real model or a cloud API
(transcription, face/emotion vision, story building, editing decisions, and
chat command interpretation) is accessed through `backend.ai.registry.get_*`
functions rather than being called directly. This keeps exactly one place
(`registry.py`) that needs to change when a local model or a cloud API key is
wired in later — nothing in backend/core/local_pipeline.py or the API routes
needs to know which mode is active.
"""
