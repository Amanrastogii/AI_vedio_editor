"""Pydantic request/response schemas for all API endpoints."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    target_duration_sec: Optional[int] = Field(None, gt=0, le=3600)
    target_style: Optional[Dict[str, Any]] = None
    output_formats: Optional[List[str]] = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    target_duration_sec: Optional[int]
    target_style: Optional[Dict]
    output_formats: Optional[List[str]]
    created_at: datetime
    completed_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}


# ── Upload ────────────────────────────────────────────────────────────────────

class InitiateUploadRequest(BaseModel):
    filename: str
    file_size_bytes: int
    content_type: str = "video/mp4"


class InitiateUploadResponse(BaseModel):
    upload_id: str
    clip_id: uuid.UUID
    s3_key: str
    presigned_url: str   # single-part for small files


class CompleteUploadRequest(BaseModel):
    clip_id: uuid.UUID
    s3_key: str


# ── Processing ────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    output_formats: Optional[List[str]] = None
    target_style: Optional[Dict[str, Any]] = None
    target_duration_sec: Optional[int] = None


class ProcessResponse(BaseModel):
    project_id: uuid.UUID
    celery_task_id: str
    message: str = "Pipeline started"


class PipelineStatusResponse(BaseModel):
    project_id: uuid.UUID
    project_status: str
    agents: List[Dict[str, Any]]


# ── Clip ─────────────────────────────────────────────────────────────────────

class ClipResponse(BaseModel):
    id: uuid.UUID
    filename: str
    original_filename: str
    duration_ms: Optional[int]
    fps: Optional[float]
    width: Optional[int]
    height: Optional[int]
    codec_video: Optional[str]
    codec_audio: Optional[str]
    file_size_bytes: Optional[int]
    upload_order: int
    is_ingested: bool
    thumbnail_url: Optional[str] = None
    source_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Output ───────────────────────────────────────────────────────────────────

class OutputResponse(BaseModel):
    id: uuid.UUID
    format: str
    aspect_ratio: str
    width: Optional[int]
    height: Optional[int]
    duration_ms: Optional[int]
    file_size_bytes: Optional[int]
    quality_score: Optional[float]
    download_url: Optional[str] = None
    render_metadata: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# ── Story / EDL ───────────────────────────────────────────────────────────────

class StoryEntryResponse(BaseModel):
    id: uuid.UUID
    position: int
    narrative_role: str
    segment_id: Optional[uuid.UUID]
    clip_id: Optional[uuid.UUID] = None
    thumbnail_url: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    trim_start_ms: Optional[int]
    trim_end_ms: Optional[int]
    transition_in: str
    edit_reasoning: Optional[str]
    effects: Optional[Dict[str, Any]] = None
    reframe_params: Optional[Dict[str, Any]] = None  # {"mode": "smart"|"center"|"fit"|"manual", "x": 0..1}
    source_url: Optional[str] = None   # raw clip file — lets the editor preview without rendering

    model_config = {"from_attributes": True}


class StoryEntryUpdate(BaseModel):
    trim_start_ms: Optional[int] = None
    trim_end_ms: Optional[int] = None
    transition_in: Optional[str] = None
    zoom_params: Optional[Dict[str, Any]] = None
    reframe_params: Optional[Dict[str, Any]] = None
    effects: Optional[Dict[str, Any]] = None
    narrative_role: Optional[str] = None


class SplitRequest(BaseModel):
    at_ms: int = Field(description="Absolute source-clip time to cut at (between trim_start and trim_end)")


class TimelineRow(BaseModel):
    segment_id: uuid.UUID
    narrative_role: Optional[str] = None
    transition_in: Optional[str] = "cut"
    trim_start_ms: Optional[int] = None
    trim_end_ms: Optional[int] = None
    effects: Optional[Dict[str, Any]] = None
    reframe_params: Optional[Dict[str, Any]] = None
    edit_reasoning: Optional[str] = None


class TimelineReplaceRequest(BaseModel):
    rows: List[TimelineRow]


class ClipRangeCreate(BaseModel):
    clip_id: uuid.UUID
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


class VersionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)


class VersionResponse(BaseModel):
    id: uuid.UUID
    label: str
    entry_count: int
    created_at: datetime


class AudioTrackUpdate(BaseModel):
    start_ms: Optional[int] = Field(None, ge=0)
    source_offset_ms: Optional[int] = Field(None, ge=0)
    length_ms: Optional[int] = Field(None, ge=0)
    volume: Optional[float] = Field(None, ge=0, le=2)
    fade_in_ms: Optional[int] = Field(None, ge=0, le=20000)
    fade_out_ms: Optional[int] = Field(None, ge=0, le=20000)
    loop: Optional[bool] = None
    duck_original: Optional[float] = Field(None, ge=0, le=1)


class AudioTrackResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    duration_ms: Optional[int]
    start_ms: int
    source_offset_ms: int
    length_ms: Optional[int]
    volume: float
    fade_in_ms: int
    fade_out_ms: int
    loop: bool
    duck_original: float
    url: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None   # {"bpm", "beats_ms", "downbeat_phase", ...} once analysed

    model_config = {"from_attributes": True}


class TextOverlayCreate(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    position: str = Field("bottom", pattern="^(top|center|bottom)$")
    font_size: int = Field(56, ge=12, le=200)
    color: str = Field("white", pattern="^(#[0-9a-fA-F]{6}|[a-zA-Z]{3,20})$")
    box: bool = True


class TextOverlayUpdate(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=300)
    start_ms: Optional[int] = Field(None, ge=0)
    end_ms: Optional[int] = Field(None, gt=0)
    position: Optional[str] = Field(None, pattern="^(top|center|bottom)$")
    font_size: Optional[int] = Field(None, ge=12, le=200)
    color: Optional[str] = Field(None, pattern="^(#[0-9a-fA-F]{6}|[a-zA-Z]{3,20})$")
    box: Optional[bool] = None


class TextOverlayResponse(BaseModel):
    id: uuid.UUID
    text: str
    start_ms: int
    end_ms: int
    position: str
    font_size: int
    color: str
    box: bool

    model_config = {"from_attributes": True}


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    target_duration_sec: Optional[int] = Field(None, gt=0, le=3600)
    target_style: Optional[Dict[str, Any]] = None
    output_formats: Optional[List[str]] = None


# ── Style learning ────────────────────────────────────────────────────────────

class StyleProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class StyleAssetResponse(BaseModel):
    id: uuid.UUID
    kind: str
    original_filename: str
    duration_ms: Optional[int]
    width: Optional[int]
    height: Optional[int]

    model_config = {"from_attributes": True}


class StyleExampleResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    analysis: Optional[Dict[str, Any]]
    error_message: Optional[str]
    assets: List[StyleAssetResponse] = []
    created_at: datetime


class StyleProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    status: str
    style: Optional[Dict[str, Any]]
    metrics: Optional[Dict[str, Any]]
    summary: Optional[str]
    error_message: Optional[str]
    trained_at: Optional[datetime]
    created_at: datetime
    example_count: int = 0
    examples: Optional[List[StyleExampleResponse]] = None


class ApplyStyleRequest(BaseModel):
    profile_id: uuid.UUID
    target_duration_sec: Optional[int] = Field(None, gt=0, le=3600)


class StoryEntryCreate(BaseModel):
    segment_id: uuid.UUID
    narrative_role: Optional[str] = None
    transition_in: Optional[str] = "cut"


class StoryReorderRequest(BaseModel):
    entry_ids: List[uuid.UUID] = Field(min_length=1)


class SegmentResponse(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    start_ms: int
    end_ms: int
    segment_type: str
    quality_score: Optional[float]
    engagement_score: Optional[float]
    has_face: bool
    keyframe_url: Optional[str] = None
    on_timeline: bool = False
    source_url: Optional[str] = None

    model_config = {"from_attributes": True}


class RenderRequest(BaseModel):
    output_formats: Optional[List[str]] = None


class RenderResponse(BaseModel):
    total_duration_ms: int
    any_failed: bool
    qa_passed: bool
    warnings: List[str] = []


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    action_json: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}
