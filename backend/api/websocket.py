"""
WebSocket endpoint — relays event-bus messages to connected frontend clients.

Works in both LOCAL_MODE (in-memory broker) and production (Redis) because it
goes through the unified EventBus.

Frontend connects to /ws/projects/{project_id}?token=<jwt> and receives
real-time progress events from all 11 agents.
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from backend.config import settings
from backend.core.event_bus import EventBus
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ProjectRepository

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/projects/{project_id}")
async def project_websocket(
    websocket: WebSocket,
    project_id: str,
    token: str = Query(...),
):
    # Authenticate before accepting.
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str = payload.get("sub")
        if not user_id_str:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Verify the token's user actually owns this project (REST routes already
    # enforce this; the socket previously let any valid JWT subscribe to any
    # project's channel).
    try:
        async with AsyncSessionLocal() as session:
            project = await ProjectRepository(session).get(uuid.UUID(project_id))
        if not project or str(project.user_id) != user_id_str:
            await websocket.close(code=4003, reason="Not authorized for this project")
            return
    except ValueError:
        await websocket.close(code=4004, reason="Invalid project id")
        return

    await websocket.accept()
    logger.info("WS connected: project=%s", project_id)

    bus = EventBus()
    channel = f"project:{project_id}"

    try:
        async for data in bus.subscribe(channel):
            await websocket.send_json(data)
            if data.get("event") in ("pipeline.complete", "pipeline.failed"):
                # Give the client a moment to render the final state.
                await asyncio.sleep(0.2)
                break
    except WebSocketDisconnect:
        logger.info("WS disconnected: project=%s", project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("WS error: %s", e)
    finally:
        await bus.close()
        try:
            await websocket.close()
        except Exception:
            pass
