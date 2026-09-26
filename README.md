# 🎬 AI Video Editor

An AI-assisted video editing platform: upload raw clips, and an **11-agent pipeline** analyses them, builds a story, makes editing decisions and renders finished videos for YouTube, Shorts, Reels, TikTok and LinkedIn. A Premiere-style manual editor, caption tooling and "teach the AI how you edit" style learning sit on top of the same REST API.

Backend: **FastAPI · SQLAlchemy (async) · Celery · Redis · PostgreSQL/SQLite · FFmpeg** &nbsp;|&nbsp; Frontend: **Next.js · TypeScript · Tailwind CSS**

## ✨ Features

- **Multi-agent pipeline (11 agents):** ingestion → scene detection → speech ∥ face ∥ emotion → story builder → editing decision → audio ∥ subtitle → rendering → quality assurance, with parallel stages and live progress.
- **Real-time progress:** WebSocket events (`pipeline.started`, `agent.progress`, `pipeline.complete`) or a polling endpoint.
- **Multi-format output:** 16:9 and 9:16 renders (YouTube, Shorts, Reels, TikTok, LinkedIn) with a per-output quality score.
- **Manual editor (REST):** trim, razor split, duplicate, join, speed/volume/colour effects, real FFmpeg `xfade` transitions, music and voice-over tracks with ducking and fades, text overlays, timeline versions and atomic undo/redo.
- **Captions and clean-up:** local speech-to-text (faster-whisper) with karaoke/bold/classic caption styles, SRT/VTT export, silence and filler-word removal.
- **Beat sync and smart reframing:** snap cuts to the music beat grid; subject-following crops for 16:9 → 9:16.
- **Editor style learning:** upload finished edits with their raw clips; the system aligns them by perceptual hashing, learns keep/cut decisions and applies that style to new footage.
- **Chat commands:** natural-language editing ("remove silences", "make it 30 seconds", "use dissolve transitions"), optionally interpreted by Claude.
- **Auth and multi-tenancy:** JWT authentication, per-user projects and files.

## 🏗️ Architecture

```
Next.js UI ──REST/WebSocket──▶ FastAPI (backend/api)
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
        Agent pipeline        Editing / audio /      Storage layer
       (backend/agents)       style engines         local disk ⇄ S3/MinIO
              │
   Celery workers (GPU + CPU queues) ── Redis broker ── Flower monitor
              │
   PostgreSQL (SQLite in local mode) · Qdrant vector store · FFmpeg/FFprobe
```

```
backend/
├── agents/     # ingestion, scene_detection, speech, face, emotion, story_builder,
│               # editing_decision, audio_enhancement, subtitle, rendering, quality_assurance
├── api/        # FastAPI app, routes, auth, schemas, WebSocket
├── editing/    # timeline operations, transitions, effects
├── audio/      # tracks, beat tracking, ducking
├── style/      # style-profile learning and application
├── storage/    # local and S3/MinIO backends
├── workers/    # Celery app and queues
├── database/   # models and migrations
└── ai/         # provider registry (placeholder / local / cloud)
frontend/       # Next.js app (app, components, hooks, lib)
infrastructure/ # Dockerfiles (API, CPU worker, GPU worker)
tests/          # unit and integration tests
```

## 🚀 Quick start (local mode, no API keys needed)

Local mode uses SQLite, local disk and an in-process pipeline.

```bash
# backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-local.txt
cp .env.example .env                                   # keep LOCAL_MODE defaults
python -m uvicorn backend.api.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev              # http://localhost:3000
```

Swagger UI: `http://localhost:8000/docs`. FFmpeg and FFprobe must be on your `PATH`.

**Full stack (production-style):** `docker compose up` starts PostgreSQL, Redis, MinIO, Qdrant, the API, CPU/GPU Celery workers and Flower. Set `LOCAL_MODE=false`, a Postgres `DATABASE_URL` and S3 credentials.

## 📡 API workflow

```text
POST /api/v1/auth/register                      → JWT
POST /api/v1/projects                           → project_id
POST /api/v1/projects/{id}/uploads/local        → upload clips
POST /api/v1/projects/{id}/process              → start the 11-agent pipeline
WS   /ws/projects/{id}?token=JWT                → live agent progress
GET  /api/v1/projects/{id}/outputs              → rendered videos + download URLs
```

The complete endpoint reference (manual editor, captions, style learning, chat) is in [API_GUIDE.md](API_GUIDE.md).

## ✅ What is real vs. simulated locally

| Stage | Local mode |
|---|---|
| Ingestion, scene detection, rendering, QA | **Real**: FFprobe, PySceneDetect, OpenCV, FFmpeg |
| Captions, filler-word removal | **Real**: local faster-whisper (CPU) |
| Beat sync, smart reframing, style learning | **Real**: CPU-only |
| Story and editing decisions | Heuristics by default; **Claude** when `ANTHROPIC_API_KEY` is set |
| Speech / face / emotion agents | Simulated locally (GPU models required); production path uses WhisperX, InsightFace and DeepFace |

## 🧰 Tech stack

FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · Celery · Redis · PostgreSQL · MinIO/S3 · Qdrant · FFmpeg · OpenCV · PySceneDetect · faster-whisper · Anthropic API · Next.js · TypeScript · Tailwind CSS · Docker

## 👤 Author

**Aman Rastogi**: [LinkedIn](https://www.linkedin.com/in/amanrastogi-dev) · [GitHub](https://github.com/Amanrastogii)
