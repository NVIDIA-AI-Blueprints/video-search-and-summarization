# Build from a pinned VSS repository commit: docker build -f this-file .
FROM python:3.13-slim

ARG VSS_COMMIT
LABEL org.opencontainers.image.revision=$VSS_COMMIT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/vss
COPY libs/vss/core /opt/vss/libs/vss/core
COPY libs/vss/cli /opt/vss/libs/vss/cli
COPY skills/benchmarking/benchmark-video-search/scripts /opt/vss/skills/benchmarking/benchmark-video-search/scripts
COPY services/agent/pyproject.toml /opt/vss/services/agent/pyproject.toml
COPY services/agent/packages/vss_agents/src/vss_agents/tools/search.py /opt/vss/services/agent/packages/vss_agents/src/vss_agents/tools/search.py

RUN python -m pip install --upgrade pip && \
    python -m pip install /opt/vss/libs/vss/core /opt/vss/libs/vss/cli requests && \
    useradd --uid 10001 --create-home benchmark && \
    mkdir -p /data /results && chown -R benchmark:benchmark /data /results /home/benchmark

USER 10001:10001
ENV HOME=/home/benchmark
ENTRYPOINT ["python", "/opt/vss/skills/benchmarking/benchmark-video-search/scripts/run_eval_flows.py"]
