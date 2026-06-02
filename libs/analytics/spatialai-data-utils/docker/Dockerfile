FROM python:3.13-slim AS builder

ARG PYTORCH3D_REF=33824be
ARG TORCH_VERSION=2.10.0+cpu

ENV DEBIAN_FRONTEND=noninteractive \
    FORCE_CUDA=0 \
    MAX_JOBS=4 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /spatialai_data_utils

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++ \
        git && \
    rm -rf /var/lib/apt/lists/*

COPY Pipfile ./Pipfile
COPY README.md LICENSE NOTICE 3rdParty_Licenses.md ./
COPY release ./release
COPY spatialai_data_utils ./spatialai_data_utils

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install meson-python ninja "Cython>=0.29.34,<3.1"

RUN python -m pip install "torch==${TORCH_VERSION}" \
    --extra-index-url https://download.pytorch.org/whl/cpu

RUN python -m pip install \
        ninja==1.11.1.1 \
        fvcore==0.1.5.post20221221 \
        iopath==0.1.9

RUN git clone https://github.com/facebookresearch/pytorch3d.git /tmp/pytorch3d && \
    cd /tmp/pytorch3d && \
    git checkout "${PYTORCH3D_REF}" && \
    python -m pip install --no-build-isolation . && \
    rm -rf /tmp/pytorch3d

RUN python -m pip install --no-build-isolation ./release

FROM python:3.13-slim AS runtime

ARG APP_UID=1000
ARG APP_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libglib2.0-0 \
        libgl1 \
        libgomp1 && \
    mkdir -p /workspace && \
    chown -R "${APP_UID}:${APP_GID}" /workspace && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local

WORKDIR /workspace

USER ${APP_UID}:${APP_GID}

CMD ["python"]
