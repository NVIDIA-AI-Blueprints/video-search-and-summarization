import logging

from fastapi import FastAPI, HTTPException

from app.agent import run_agent_async
from app.config import get_settings
from app.models import InvocationRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ava.agent.server")

app = FastAPI(title="video-analysis-agent")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": get_settings().bedrock_model_id}


@app.post("/invocations")
async def invocations(request: InvocationRequest) -> dict:
    logger.info("invocation video_ids=%s question_len=%d", request.video_ids, len(request.question))
    try:
        answer = await run_agent_async(request.question, request.video_ids)
        return answer.model_dump()
    except Exception as exc:
        logger.exception("agent invocation failed")
        raise HTTPException(status_code=500, detail=f"Agent failure: {exc}") from exc
