import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from client.api.router import router
from client.config import settings
from client.core.admission import Admission
from client.core.inference_client import InferenceClient
from client.shared.kafka import KafkaPublisher

logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.kafka = KafkaPublisher()
    await app.state.kafka.start()
    semaphore = asyncio.Semaphore(settings.semaphore_permits)
    app.state.inference = InferenceClient(semaphore)
    app.state.admission = Admission(settings.intake_queue_size)
    # Hold background-task references so they are not garbage collected.
    app.state.tasks = set()
    yield
    await app.state.inference.close()
    await app.state.kafka.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="AOI Client", lifespan=lifespan)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        # Reject oversized bodies before parsing.
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "request body exceeds MAX_REQUEST_BYTES"},
                )
        return await call_next(request)

    app.include_router(router)
    return app


app = create_app()
