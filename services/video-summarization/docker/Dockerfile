# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

ARG BASE_IMAGE="via-engine-base"

# Package installer: ubuntu base for shell + uv (uv is discarded; only site-packages reach final image)
FROM nvcr.io/nvidia/base/ubuntu:jammy-20250415.1 AS pkg-installer

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PIP_DEFAULT_TIMEOUT=180
ENV PIP_RETRIES=5

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# vss-ctx-rag transitively pulls opencv-python into /tmp/packages; strip it
# so the headless variant (already in the base image via pyproject.toml) wins.
# Only opencv_python-* (not opencv_python_headless-*) is matched.
RUN uv pip install \
        "vss-ctx-rag @ git+https://github.com/NVIDIA/context-aware-rag.git@release/3.0.0" \
        "vss-ctx-rag-arango @ git+https://github.com/NVIDIA/context-aware-rag.git@release/3.0.0#subdirectory=packages/vss_ctx_rag_arango" \
        --extra-index-url https://pypi.nvidia.com/ \
        --python-version 3.13 --target /tmp/packages \
 && rm -rf /tmp/packages/cv2 /tmp/packages/opencv_python.libs \
        /tmp/packages/opencv_python-*.dist-info \
 && uv pip install opencv-python-headless \
        --python-version 3.13 --target /tmp/packages

# Ubuntu builder: shell-based operations only — user creation, config/source copy scripts
FROM nvcr.io/nvidia/base/ubuntu:jammy-20250415.1 AS app-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG BUILD_COMMIT_SHA="unknown"

# Create non-root user for security best practices
ARG USER_ID=1000
ARG GROUP_ID=1000
RUN if id -u ${USER_ID} >/dev/null 2>&1; then \
        EXISTING_USER=$(id -un ${USER_ID}); \
        if [ "$EXISTING_USER" != "lvsuser" ]; then \
            usermod -l lvsuser -d /home/lvsuser -m $EXISTING_USER; \
        fi; \
    else \
        getent group ${GROUP_ID} >/dev/null 2>&1 || groupadd -g ${GROUP_ID} lvsuser; \
        useradd -u ${USER_ID} -g ${GROUP_ID} -m -s /bin/bash lvsuser; \
    fi && \
    if getent group ${GROUP_ID} >/dev/null 2>&1; then \
        EXISTING_GROUP=$(getent group ${GROUP_ID} | cut -d: -f1); \
        if [ "$EXISTING_GROUP" != "lvsuser" ]; then \
            groupmod -n lvsuser $EXISTING_GROUP; \
        fi; \
    fi

# Static shell utilities are required by CI and start_via.sh in the distroless runtime image.
# Only ca-certificates (data file, ships at runtime) and patchelf (build-time only)
# remain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates patchelf \
    && rm -rf /var/lib/apt/lists/*

# Prepare bash, lightweight shell utilities, and bash's non-core shared libraries.
RUN set -eux; \
    mkdir -p \
        /tmp/bash-runtime/etc/ssl/certs \
        /tmp/bash-runtime/usr/local/bin \
        /tmp/bash-runtime/usr/local/lib/bash-runtime; \
    cp -L /etc/ssl/certs/ca-certificates.crt /tmp/bash-runtime/etc/ssl/certs/ca-certificates.crt; \
    for binary in /bin/bash /usr/local/bin/uv; do \
        cp -L "$binary" "/tmp/bash-runtime/usr/local/bin/$(basename "$binary")"; \
        ldd "$binary" 2>/dev/null \
            | awk '/=> \// { print $3 } /^[[:space:]]*\// { print $1 }' \
            | grep -Ev '/(ld-linux[^/]*|libc|libcrypto|libssl)\.so' \
            | while read -r lib; do \
                cp -L "$lib" "/tmp/bash-runtime/usr/local/lib/bash-runtime/$(basename "$lib")"; \
            done; \
    done; \
    printf '%s\n' \
        '#!/usr/local/bin/bash' \
        'set -e' \
        'url=""' \
        'while [ "$#" -gt 0 ]; do' \
        '    case "$1" in' \
        '        -s|--silent) exec 2>/dev/null; shift ;;' \
        '        -*) shift ;;' \
        '        *) url="$1"; shift ;;' \
        '    esac' \
        'done' \
        'case "$url" in http://*) ;; *) exit 2 ;; esac' \
        'target="${url#http://}"' \
        'hostport="${target%%/*}"' \
        'path="/${target#*/}"' \
        '[ "$path" = "/$target" ] && path="/"' \
        'host="${hostport%%:*}"' \
        'port="${hostport##*:}"' \
        '[ "$port" = "$hostport" ] && port=80' \
        'exec 3<>"/dev/tcp/$host/$port"' \
        'printf "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" "$path" "$host" >&3' \
        'body=false' \
        'while IFS= read -r line <&3; do' \
        '    line="${line%$'\''\r'\''}"' \
        '    if [ "$body" = true ]; then' \
        '        printf "%s\n" "$line"' \
        '    elif [ -z "$line" ]; then' \
        '        body=true' \
        '    fi' \
        'done' \
        > /tmp/bash-runtime/usr/local/bin/curl; \
    chmod 755 /tmp/bash-runtime/usr/local/bin/curl; \
    patchelf --set-rpath '$ORIGIN/../lib/bash-runtime' \
        /tmp/bash-runtime/usr/local/bin/bash; \
    find /tmp/bash-runtime/usr/local/lib/bash-runtime -type f \
        -exec sh -c 'for lib; do patchelf --set-rpath "$ORIGIN" "$lib" 2>/dev/null || true; done' sh {} +; \
    if find /tmp/bash-runtime/usr/local/lib/bash-runtime \
        \( -name 'libcrypto*.so*' -o -name 'libssl*.so*' \) \
        | grep -q .; then \
        echo "OpenSSL libraries must not be copied into bash-runtime"; \
        exit 1; \
    fi; \
    printf '%s\n' \
        '#!/usr/local/bin/bash' \
        'set -e' \
        'export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"' \
        'if [ -n "${PIP_INDEX_URL:-}" ] && [ -z "${UV_INDEX_URL:-}" ]; then' \
        '    export UV_INDEX_URL="$PIP_INDEX_URL"' \
        'fi' \
        'if [ "${1:-}" = "install" ]; then' \
        '    shift' \
        '    args=()' \
        '    for arg in "$@"; do' \
        '        [ "$arg" = "--no-cache-dir" ] && continue' \
        '        args+=("$arg")' \
        '    done' \
        '    exec /usr/local/bin/uv pip install --system "${args[@]}"' \
        'fi' \
        'exec python3 -m pip "$@"' \
        > /tmp/bash-runtime/usr/local/bin/pip; \
    chmod 755 /tmp/bash-runtime/usr/local/bin/pip; \
    ln -s pip /tmp/bash-runtime/usr/local/bin/pip3

# Create /opt/nvidia/via so copy_configs can write there (src/ copy moved to end for cache reuse)
RUN mkdir -p /opt/nvidia/via
RUN --mount=type=bind,source=config,target=/tmp/via-src/config \
    --mount=type=bind,source=docker,target=/tmp/via-src/docker \
    cd /tmp/via-src/ \
    && docker/copy_configs.sh config/ /opt/nvidia/via/

# Create parent directories with recursive write permissions for non-root user
# All subdirectories created at runtime will inherit proper ownership
RUN mkdir -p /home/lvsuser/.via/ngc_model_cache && \
    mkdir -p /home/lvsuser/.milvus.io && \
    mkdir -p /home/lvsuser/.cache/uv && \
    chown -R lvsuser:lvsuser /home/lvsuser/.via && \
    chown -R lvsuser:lvsuser /home/lvsuser/.milvus.io && \
    chown -R lvsuser:lvsuser /home/lvsuser/.cache/uv && \
    chmod -R 755 /home/lvsuser/.via && \
    chmod -R 755 /home/lvsuser/.milvus.io

# Copy app source last so only this layer rebuilds when only src/ changes (runs as root; must be before USER)
RUN --mount=type=bind,source=src,target=/tmp/via-src/src \
    --mount=type=bind,source=docker,target=/tmp/via-src/docker \
    cd /tmp/via-src/ \
    && docker/copy_sources.sh src/ /opt/nvidia/via/
RUN echo "${BUILD_COMMIT_SHA}" > /opt/nvidia/via/BUILD_COMMIT_SHA

# Final distroless production image — both builder stages are discarded
FROM $BASE_IMAGE

# vss-ctx-rag Python packages from pkg-installer
# (uv ships separately via app-builder's bash-runtime bundle below)
COPY --from=pkg-installer /tmp/packages/ /usr/local/lib/python3.13/site-packages/

# App configs and sources.
COPY --from=app-builder /opt/nvidia/via /opt/nvidia/via

# User home directories with correct ownership
COPY --from=app-builder /home/lvsuser /home/lvsuser

# Bash runtime required by start_via.sh in the distroless production image
COPY --from=app-builder /tmp/bash-runtime/etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=app-builder /tmp/bash-runtime/usr/local/bin/bash /usr/local/bin/bash
COPY --from=app-builder /tmp/bash-runtime/usr/local/bin/curl /usr/local/bin/curl
COPY --from=app-builder /tmp/bash-runtime/usr/local/bin/pip /usr/local/bin/pip
COPY --from=app-builder /tmp/bash-runtime/usr/local/bin/pip3 /usr/local/bin/pip3
COPY --from=app-builder /tmp/bash-runtime/usr/local/bin/uv /usr/local/bin/uv
COPY --from=app-builder /tmp/bash-runtime/usr/local/lib/bash-runtime/ /usr/local/lib/bash-runtime/
# (busybox + chmod/find/grep/mkdir/rm symlinks intentionally not shipped — see the
#  app-builder apt-install comment for rationale. The libcrypto/libssl removal RUN was
#  also dropped: app-builder's `find … libcrypto*/libssl* && exit 1` guard already
#  fails the build if those leak into bash-runtime, so the cleanup was redundant.)
ENV LD_LIBRARY_PATH=
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV PATH=/usr/local/bin:${PATH}:/opt/nvidia/via/
RUN ["/usr/local/bin/bash", "-c", "test ! -e /usr/local/lib/bash-runtime/libcrypto.so.3 && test ! -e /usr/local/lib/bash-runtime/libssl.so.3 && python3 -c 'import ssl'"]

# User account entries so USER lvsuser resolves correctly in distroless
COPY --from=app-builder /etc/passwd /etc/passwd
COPY --from=app-builder /etc/group /etc/group

COPY start_via.sh /opt/nvidia/via/
COPY VERSION /opt/nvidia/via/

COPY LICENSE /opt/mm/LICENSE
LABEL com.nvidia.mm.nspect="NSPECT-MZI5-0FE7"

WORKDIR /opt/nvidia/via/

# Switch to non-root user for runtime
USER lvsuser

ENTRYPOINT ["/usr/local/bin/bash", "/opt/nvidia/via/start_via.sh"]
