# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI application entrypoint for the standalone Model Manager service."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.endpoints import router as api_router
from src.api.errors import install_error_handlers
from src.config import log_reclaim_posture

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Say which disk-reclaim posture this process came up in, then serve."""
    log_reclaim_posture()
    yield


app = FastAPI(
    title="DFW Model Promotion API",
    description="Standalone AOI Model Manager implementing the Model Promotion API.",
    version="0.1.0",
    lifespan=lifespan,
)

install_error_handlers(app)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8090)))
