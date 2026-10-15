# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Stage that materializes only the paths listed in packagelist.txt. Docker
# COPY has no "copy these paths from a list" form; doing the selection here
# keeps the runtime image free of unlisted files without a per-file COPY.
FROM busybox:1.36 AS app_src
COPY docker/packagelist.txt /packagelist.txt
COPY src /ctx/src
RUN set -eu; \
    grep -vE '^[[:space:]]*(#|$)' /packagelist.txt > /tmp/paths; \
    while IFS= read -r p; do \
        p="${p%/}"; \
        if [ -d "/ctx/$p" ]; then \
            mkdir -p "/out/$p"; \
            cp -a "/ctx/$p/." "/out/$p/"; \
        elif [ -f "/ctx/$p" ]; then \
            mkdir -p "/out/$(dirname "$p")"; \
            cp -a "/ctx/$p" "/out/$p"; \
        else \
            echo "packagelist.txt: missing $p" >&2; \
            exit 1; \
        fi; \
    done < /tmp/paths; \
    chmod +x /out/src/start_service.sh

# NGC PyTorch base ships CUDA, cuDNN, PyTorch, and TensorRT pre-matched.
# Do NOT reinstall torch/torchvision/torchaudio/tensorrt on top of this base
# (would clash with the CUDA build that's already inside).
FROM nvcr.io/nvidia/pytorch:25.11-py3 AS base

# NGC PyTorch sets PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python in the
# base image, which forces the pure-Python protobuf parser. That parser
# is 50-100x slower than the upb C parser and was the root cause of
# Kafka consumer lag.
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=upb

# main.py opens its Milvus channel at import, in the parent, before forking the
# Kafka consumer / maintenance / search subprocesses. gRPC only survives that
# ordering when this is set: it registers the atfork handlers that reset gRPC's
# core state in the child, so the child's own channel works. Without it the
# child inherits the parent's dead pollers and every connect fails (pymilvus
# 3.0.1) or hangs forever (3.0.0) -- which silently kills Kafka ingestion.
ENV GRPC_ENABLE_FORK_SUPPORT=true

# Python block-buffers stdout when it is a pipe, which is what Docker gives the
# container, so `docker logs` lags behind and loses the tail on a hard stop.
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Docker-specific packages: ONLY what NGC doesn't already provide.
# Direct deps and the NGC exclude list live in docker/py_deps/pyproject.toml.
# uv.lock is the lockfile. uv is mounted for this step and is not left in
# the image. BuildKit hashes the mounted directory, so a lock change
# re-runs the install. --frozen refuses a lock that does not match
# pyproject.toml.
RUN --mount=type=bind,from=ghcr.io/astral-sh/uv:0.12.1,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,source=docker/py_deps,target=/tmp/py_deps \
    cd /tmp/py_deps && \
    UV_PYTHON_DOWNLOADS=never UV_NO_MANAGED_PYTHON=1 \
    uv export --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt && \
    uv pip install --system --break-system-packages --no-cache \
        --python /usr/bin/python3 -r /tmp/requirements.txt

# ---------------------------------------------------------------------------
# nSpect CVE remediation for the base image (NSPECT-5P1D-LPOG).
#
# Almost none of the Critical/High findings against this image come from
# first-party code or from the pinned requirements: they are packages the NGC
# base ships. The lock in docker/py_deps/uv.lock covers only what we install
# ourselves, so the rest has to be dealt with here, in four groups.
#
# Two of them are removals rather than upgrades. That is deliberate: for a
# component this container can never execute, deleting it removes the
# vulnerability instead of restating it as an accepted risk, and it is the
# only available answer for a package upstream has not fixed.
#
# This block is last in `base` on purpose -- editing a package list here must
# not invalidate the expensive pip layers above.
#
# Verified against 25.11 before committing: `pip check` stays clean, torch /
# torchvision / tensorrt / onnx still import, torch.onnx.export still runs,
# protobuf still resolves to the upb parser that the
# PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION setting at the top of this file
# depends on, and the service imports are untouched.
# ---------------------------------------------------------------------------

# 1. OS packages. openssl and the gnupg family are the only dpkg findings, and
#    Ubuntu's archive is already past both fix versions.
RUN apt-get update && apt-get install -y --only-upgrade --no-install-recommends \
        libssl3t64 libssl-dev openssl \
        gnupg gnupg-utils gpg gpg-agent gpgconf gpgsm gpgv dirmngr keyboxd \
    && rm -rf /var/lib/apt/lists/*

# 2. Nsight Systems' EFA metrics plugin. Its `nic_sampler` is a Go binary built
#    against go1.23.4, and that single binary carries 36 of the flagged CVE ids
#    -- including three of the four Criticals -- all of them Go stdlib. It
#    samples AWS EFA NIC counters for the profiler, so no code path in a ReID
#    inference container reaches it.
RUN rm -rf /usr/local/cuda-*/NsightSystems-cli-*/target-*/plugins/efa_metrics

# 3. The notebook stack, and flash-attn. The service does not import Jupyter
#    or flash-attn, and nothing in /opt/nvidia/entrypoint.d references Jupyter,
#    so this whole tree is dead weight that happens to carry the remaining
#    Critical (jupyter-server GHSA-fcw5-x6j4-ccmp) plus the tornado, mistune,
#    nbconvert and soupsieve findings. tornado is uninstalled last because these are its only
#    consumers. flash-attn is here because it is the one flagged package with
#    no upstream fix at all, and nothing imports it.
RUN python3 -m pip uninstall -y \
        jupyterlab_tensorboard_pro jupyterlab_code_formatter jupytext \
        notebook notebook_shim jupyterlab jupyterlab_server jupyter-lsp \
        jupyter_server jupyter_server_terminals terminado \
        nbconvert nbclient nbformat jupyterlab_pygments mistune \
        beautifulsoup4 soupsieve ipykernel jupyter_client tornado \
        flash_attn black

# 4. Upgrade what remains and is still reachable. pillow and protobuf are the
#    two the requirements files warn against touching, so they are the two that
#    were exercised hardest: 12.0.0 -> 12.3.0 keeps torchvision's PIL interop
#    working, and 6.33.0 -> 6.33.5 stays inside the same minor, so the upb
#    parser and ds_schema_pb2.py behave identically. setuptools is listed for
#    its *vendored* copies -- the findings are against
#    setuptools/_vendor/wheel-0.45.1 and jaraco.context 5.3.0, which upgrading
#    the top-level packages alone would leave in place.
RUN python3 -m pip install --no-cache-dir --upgrade \
        'pillow==12.3.0' \
        'protobuf==6.33.5' \
        'urllib3==2.7.0' \
        'aiohttp==3.14.3' \
        'msgpack==1.2.1' \
        'pyarrow==23.0.1' \
        'onnx==1.21.0' \
        'wheel==0.46.2' \
        'setuptools>=80'

# 4b. The four remaining High findings (GHSA-hqmj-h5c6-369m,
#    GHSA-3r9x-f23j-gc73, GHSA-538c-55jv-c5g9, GHSA-q56x-g2fj-4rj6) are
#    not the importable onnx 1.21.0 installed above. Anchore inventories
#    /opt/pytorch/pytorch/third_party/onnx/onnx.egg-info as onnx 1.18.0
#    even though that tree is not on sys.path and torch.onnx.export uses
#    dist-packages. Deleting the vendored copy is what actually clears
#    those IDs; upgrading pip onnx alone does not.
RUN rm -rf /opt/pytorch/pytorch/third_party/onnx

# 5. IP / OSRB codec strip (NVBug 6732967). check_dockers.sh greps for
#    substrings in .so names, so most of its FAILs are not royalty codecs
#    and must stay: OpenSSL (libssl/libcrypto), GCC Fortran runtime
#    (libgfortran/libquadmath for numpy/scipy/faiss), libpng (Pillow /
#    torchvision), Pillow's bundled libavif (AVIF is royalty-free), and
#    the torchvision JPEG whose build-id hash contains "265".
#
#    What we *can* drop is unused audio/video decode that ReID never
#    calls (Kafka embeddings + Milvus; no GStreamer, no FFmpeg CLI):
#    Debian mpg123/lame/FLAC/sox, and NGC's 3.12 nvidia-dali which is
#    the only source of libavcodec/libavformat/libavfilter/libswscale
#    in this image. DALI's FFmpeg is not linked to x264/x265 (the
#    script already PASSed that check) but the libs still trip the
#    filename grep. The service does not import DALI.
#
#    Do not apt-purge libssl / libgfortran / libpng. Do not delete
#    Nsight's OpenSSL copies for this bug (they are crypto, not codecs;
#    the EFA plugin was already removed above for CVEs).
RUN apt-get purge -y --auto-remove \
        libmpg123-0t64 libmpg123-0 libmp3lame0 libflac12 sox \
    || true \
    && rm -f \
        /usr/lib/x86_64-linux-gnu/libmpg123.so* \
        /usr/lib/x86_64-linux-gnu/libmp3lame.so* \
        /usr/lib/x86_64-linux-gnu/libFLAC.so* \
        /usr/lib/x86_64-linux-gnu/sox/libsox_fmt_flac.so \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip uninstall -y \
        nvidia-dali nvidia-dali-cuda120 nvidia-dali-cuda130 \
        nvidia-dali-cuda110 \
    || true \
    && rm -rf /usr/local/lib/python3.12/dist-packages/nvidia/dali

# Third-party attribution, published inside the container at /app/LICENSE.3rdparty.
# Reproducing the licence text of every bundled component -- not just naming the
# licence -- is the attribution obligation under MIT/BSD/Apache-2.0, and the OSRB
# requires the file ship in the deliverable. Regenerate with
# utils/generate_third_party_notices.sh whenever docker/py_deps/uv.lock changes.
COPY LICENSE.3rdparty .

# Documents the default bind port only. The service binds REID_SERVICE_PORT at
# runtime (see src/config.py). On the host network used by docker/compose.yaml
# that port is reachable on the host directly.
EXPOSE 8000

# `requests.get` returns a Response on any status code, so the original
# health check would never fail. raise_for_status() makes it actually fail
# on non-2xx responses.
#
# Shell form (not exec form) so ${REID_SERVICE_PORT} is expanded by /bin/sh
# from the container environment at probe time, tracking the bound port.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests, sys; r = requests.get('http://localhost:${REID_SERVICE_PORT:-8000}/'); sys.exit(0 if r.ok else 1)"

CMD ["./src/start_service.sh"]

# ---------------------------------------------------------------------------
# Application code.
#
# Docker COPY cannot take a file list, so the app_src stage copies only the
# paths in packagelist.txt. That keeps dataset dumps, archives, scratch
# notebooks, and CLI-only modules out of the runtime image. The staged tree
# lands at /app so that paths derived from __file__ (see
# secondary_embedding_generation) resolve to the same place inside and outside
# the container. src/start_service.sh runs `python src/main.py` from /app, which puts
# /app/src (not /app) on sys.path, so the flat imports (`import config`,
# `from cluster_manager import ...`) keep working.
# ---------------------------------------------------------------------------
FROM base
COPY --from=app_src /out/ /app/
