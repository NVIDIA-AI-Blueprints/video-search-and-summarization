from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.models.video import (
    VideoCreatedResponse,
    VideoListResponse,
    VideoMetadata,
)
from app.services.agent_client import AgentClientError
from app.services.db import VideoRepository
from app.services.storage import StorageService

router = APIRouter(prefix="/v1/videos", tags=["videos"])

_db = VideoRepository()
_storage = StorageService()


@router.post("", response_model=VideoCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_video(
    filename: str,
    content_type: str = "video/mp4",
    title: str | None = None,
    user: dict = Depends(get_current_user),
) -> VideoCreatedResponse:
    if not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="content_type must be a video/* type")

    video = VideoMetadata(owner_id=user["user_id"], filename=filename, content_type=content_type, title=title)
    key = _storage.media_key(video.owner_id, video.video_id, filename)
    upload_url = _storage.presign_upload(key, content_type)
    video.size_bytes = 0
    _db.create(video)
    return VideoCreatedResponse(video=video, upload_url=upload_url)


@router.get("", response_model=VideoListResponse)
async def list_videos(user: dict = Depends(get_current_user)) -> VideoListResponse:
    videos = _db.list_for_owner(user["user_id"])
    return VideoListResponse(videos=videos)


@router.get("/{video_id}", response_model=VideoMetadata)
async def get_video(video_id: str, user: dict = Depends(get_current_user)) -> VideoMetadata:
    video = _db.get(user["user_id"], video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(video_id: str, user: dict = Depends(get_current_user)) -> None:
    video = _db.get(user["user_id"], video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    _storage.delete_prefix(f"videos/{user['user_id']}/{video_id}/")
    _storage.delete_prefix(f"artifacts/{video_id}/")
    _db.delete(user["user_id"], video_id)


@router.get("/{video_id}/stream-url")
async def get_stream_url(video_id: str, user: dict = Depends(get_current_user)) -> dict:
    video = _db.get(user["user_id"], video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"url": _storage.media_stream_url(video.owner_id, video.video_id, video.filename)}
