from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user
from app.models.video import ChatRequest, ChatResponse
from app.services.agent_client import AgentClientError, ask_agent
from app.services.db import VideoRepository

router = APIRouter(prefix="/v1/agent", tags=["agent"])

_db = VideoRepository()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: dict = Depends(get_current_user)) -> ChatResponse:
    for video_id in request.video_ids:
        if _db.get(user["user_id"], video_id) is None:
            raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
    try:
        return await ask_agent(request.question, request.video_ids, "")
    except AgentClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
