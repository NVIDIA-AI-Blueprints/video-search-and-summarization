# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim-bookworm
WORKDIR /opt/d1-sample
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt
COPY publish.py .
USER 65534:65534
ENTRYPOINT ["python", "/opt/d1-sample/publish.py"]
