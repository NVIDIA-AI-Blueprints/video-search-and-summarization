import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from al_archiver.api.router import router
from al_archiver.config import settings
from al_archiver.core.consumer import ArchiverConsumer
from al_archiver.core.forwarder import Forwarder

logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    forwarder = Forwarder()
    app.state.consumer = ArchiverConsumer(forwarder)
    await app.state.consumer.start()
    yield
    await app.state.consumer.stop()
    await forwarder.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AL Archiver", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
