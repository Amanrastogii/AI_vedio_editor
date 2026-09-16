"""AI prompt/chat box endpoints — see backend/ai/providers.py for the interpreter."""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai import registry as ai
from backend.api.auth import get_current_user
from backend.api.schemas import ChatMessageRequest, ChatMessageResponse
from backend.database.db import get_db
from backend.database.models import ChatRole, User
from backend.database.repositories import ChatRepository, ProjectRepository

router = APIRouter(prefix="/projects/{project_id}/chat", tags=["chat"])


@router.get("", response_model=List[ChatMessageResponse])
async def get_chat_history(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await ProjectRepository(db).get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return await ChatRepository(db).list_for_project(project_id)


@router.post("", response_model=ChatMessageResponse)
async def send_chat_message(
    project_id: uuid.UUID,
    body: ChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await ProjectRepository(db).get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    chat_repo = ChatRepository(db)
    await chat_repo.add(project_id, ChatRole.USER, body.message)

    interpreter = ai.get_interpreter_provider()
    result = await interpreter.interpret(body.message, str(project_id))

    assistant_msg = await chat_repo.add(project_id, ChatRole.ASSISTANT, result.reply, action_json=result.action)
    return assistant_msg
