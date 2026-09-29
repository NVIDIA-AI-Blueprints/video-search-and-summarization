# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY segmentation/requirements.lock /tmp/requirements.lock
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements.lock
WORKDIR /app
COPY segmentation /app/segmentation
COPY streaming /app/streaming
COPY backend/neural_vision.py /app/streaming/reference/neural_vision.py
COPY backend/neural_measurement_calibration.json /app/streaming/reference/neural_measurement_calibration.json
ENV LIVE_STREAMS_CONFIG=/config/streams.json LIVE_STATE_DB=/state/live.sqlite3 LIVE_FRAME_QUEUE_SIZE=4 LIVE_MAX_QUEUE_AGE_SECONDS=0.5
EXPOSE 8092
CMD ["python", "-m", "uvicorn", "streaming.service:app", "--host", "0.0.0.0", "--port", "8092"]
