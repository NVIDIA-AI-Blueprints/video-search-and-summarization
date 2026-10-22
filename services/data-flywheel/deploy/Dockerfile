ARG BASE_IMAGE_URL=nvcr.io/nvidia/base/ubuntu
ARG BASE_IMAGE_TAG=jammy-20250415.1

FROM ${BASE_IMAGE_URL}:${BASE_IMAGE_TAG} AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Install required ubuntu packages for setting up python
# apt upgrade ensures all system packages (including gpgv) get security patches
RUN apt update && \
    apt upgrade -y && \
    apt install -y curl software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update && apt install -y python3.12 && \
    apt-get clean

# Install pip for python3.12
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

RUN rm -rf /var/lib/apt/lists/*

# Uninstall build packages and their config files (CVE-2025-68973)
# apt purge removes packages AND their config files (no rc residuals)
# apt autoremove --purge removes orphaned dependencies AND their config files
RUN apt purge -y curl software-properties-common && \
    apt autoremove --purge -y

# Download the sources of apt packages within the container for standard legal compliance
RUN sed -i 's/# deb-src/deb-src/g' /etc/apt/sources.list && \
    # Enable source downloads for deadsnakes PPA
    sed -i 's/# deb-src/deb-src/g' /etc/apt/sources.list.d/deadsnakes-ubuntu-ppa-jammy.list && \
    apt update && \
    apt install xz-utils -y && \
    mkdir -p /legal/source && \
    cd /legal/source && \
    # Download sources for all installed packages, including from PPAs
    apt list --installed | grep -i installed | sed 's|\(.*\)/.*|\1|' | xargs -r apt source --download-only || true && \
    rm -f xz-utils*

WORKDIR /legal/source
COPY LICENSE-3rd-party.txt /legal/

# Install uv
RUN pip install uv

# Copy dependency file
COPY uv.lock ./
COPY pyproject.toml ./

# Set default port
ENV PORT=8000

# RRA commented out
# RUN --mount=type=bind,source=src/agent/requirements.txt,target=/opt/agent/requirements.txt \
#     pip3 install --no-cache-dir -r /opt/agent/requirements.txt

# Backend selection: install only the chosen backend's deps (e.g. nemo-platform
# is in the `nmp` extra only). A `deft` image therefore ships no nemo-platform.
# Override at build time: `docker build --build-arg BACKEND=deft ...`.

# Development target
FROM base AS dev
ARG BACKEND=nmp
# Promote the build-time backend to a runtime env var so the app's backend
# selection (factory.get_backend_kind: BACKEND env -> settings.backend.kind ->
# "nmp") defaults to the backend this image was actually built for. Without this
# a deft image would fall back to "nmp" at runtime and fail (no nemo-platform).
# Still overridable at runtime via compose `environment:` / `-e BACKEND=...`.
ENV BACKEND=${BACKEND}
RUN uv sync --extra ${BACKEND}
RUN uv pip freeze > requirements.txt
COPY src/ ./src/
COPY config/ ./config/
# Ship only the selected backend's source: keep backends/base (shared ABC) and
# backends/${BACKEND}; drop every other backend package so a deft image carries
# no NMP code (and vice versa). Generalizes to future backends.
# Disabled for now (deps are already gated by `uv sync --extra ${BACKEND}`);
# uncomment to also prune the inactive backend's source once DEFT lands.
# RUN find src/backends -mindepth 1 -maxdepth 1 -type d \
#         ! -name base ! -name "${BACKEND}" -exec rm -rf {} +
CMD ["sh", "-c", "uv run uvicorn src.app:app --host 0.0.0.0 --port ${PORT}"]

# Production target -- default target
FROM base AS prod
ARG BACKEND=nmp
# Promote the build-time backend to a runtime env var so the app's backend
# selection (factory.get_backend_kind: BACKEND env -> settings.backend.kind ->
# "nmp") defaults to the backend this image was actually built for. Without this
# a deft image would fall back to "nmp" at runtime and fail (no nemo-platform).
# Still overridable at runtime via compose `environment:` / `-e BACKEND=...`.
ENV BACKEND=${BACKEND}
RUN uv sync --no-dev --extra ${BACKEND}
RUN uv pip freeze > requirements.txt
COPY src/ ./src/
COPY config/ ./config/
# Ship only the selected backend's source: keep backends/base (shared ABC) and
# backends/${BACKEND}; drop every other backend package so a deft image carries
# no NMP code (and vice versa). Generalizes to future backends.
# Disabled for now (deps are already gated by `uv sync --extra ${BACKEND}`);
# uncomment to also prune the inactive backend's source once DEFT lands.
# RUN find src/backends -mindepth 1 -maxdepth 1 -type d \
#         ! -name base ! -name "${BACKEND}" -exec rm -rf {} +
CMD ["sh", "-c", "uv run uvicorn src.app:app --host 0.0.0.0 --port ${PORT}"]
