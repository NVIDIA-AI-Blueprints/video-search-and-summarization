# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_GATEWAY_BIND_HOST=0.0.0.0 \
    AGENT_GATEWAY_PORT=8090

RUN groupadd --system gateway \
    && useradd --system --gid gateway --home-dir /opt/vss-agent-gateway gateway

WORKDIR /opt/vss-agent-gateway
COPY --chown=gateway:gateway vss_agent_gateway ./vss_agent_gateway

USER gateway
EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import json,os,urllib.request; h=os.environ.get('AGENT_GATEWAY_BIND_HOST','127.0.0.1'); h='127.0.0.1' if h=='0.0.0.0' else h; p=os.environ.get('AGENT_GATEWAY_PORT','8090'); assert json.load(urllib.request.urlopen(f'http://{h}:{p}/healthz', timeout=2))['status'] == 'ok'"]

ENTRYPOINT ["python", "-m", "vss_agent_gateway"]
