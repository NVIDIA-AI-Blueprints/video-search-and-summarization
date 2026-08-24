import httpx

from app.core.config import get_settings
from app.models.video import ChatResponse


class AgentClientError(RuntimeError):
    pass


async def ask_agent(question: str, video_ids: list[str], access_token: str) -> ChatResponse:
    settings = get_settings()
    payload = {"question": question, "video_ids": video_ids}
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout_seconds) as client:
            response = await client.post(
                f"{settings.agent_service_url}/invocations",
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return ChatResponse.model_validate(response.json())
    except httpx.HTTPError as exc:
        raise AgentClientError(f"Agent service call failed: {exc}") from exc
