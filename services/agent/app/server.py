from fastapi import FastAPI, HTTPException

from app.agent import run_agent_async
from app.config import get_settings

app = FastAPI(title="video-analysis-agent")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": get_settings().bedrock_model_id}


@app.post("/invocations")
async def invocations(payload: dict) -> dict:
    question = payload.get("question")
    video_ids = payload.get("video_ids", [])
    if not question or not isinstance(question, str):
        raise HTTPException(status_code=400, detail="'question' is required")

    try:
        return await run_agent_async(question, [str(v) for v in video_ids])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent failure: {exc}") from exc
