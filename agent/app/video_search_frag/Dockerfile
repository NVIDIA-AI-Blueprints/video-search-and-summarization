# Extension Dockerfile for video_search_frag
# Layers Enterprise RAG + HITL LVS tools on top of the base vss-agent image.
#
# Two-step build:
#   1. Build base:  docker build -f docker/Dockerfile -t vss-agent-base .
#   2. Build frag:  docker build -f app/video_search_frag/Dockerfile \
#                     --build-arg BASE_IMAGE=vss-agent-base -t vss-agent-frag .
#
# Build context must be the repository root.

ARG BASE_IMAGE

# --- Stage 1: Pull the pre-built base vss-agent image ---
FROM ${BASE_IMAGE} AS base

# --- Stage 2: Extend the base venv with frag packages ---
FROM python:3.13-bookworm AS frag-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

COPY --from=base /vss-agent/.venv /vss-agent/.venv

WORKDIR /vss-agent

COPY app/video_search_frag /vss-agent/app/video_search_frag

RUN . .venv/bin/activate && \
    uv pip install --no-deps /vss-agent/app/video_search_frag && \
    uv pip install --no-deps "nvidia-rag" && \
    uv pip install \
      "minio" \
      "lark>=1.2.2" \
      "bleach>=6.2,<7.0" \
      "dataclass-wizard>=0.27,<1.0" \
      "redis>=4.3.4" \
      "pymilvus-model>=0.3,<1.0" \
      "pdfplumber>=0.11.9"

# --- Stage 3: Final image = base + frag overlay ---
FROM ${BASE_IMAGE}

ARG USER_ID=1000
ARG GROUP_ID=1000

COPY --from=frag-builder --chown=${USER_ID}:${GROUP_ID} /vss-agent/.venv /vss-agent/.venv

COPY --chown=${USER_ID}:${GROUP_ID} app/video_search_frag/configs /vss-agent/configs/video_search_frag
