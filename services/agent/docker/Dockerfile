# Multi-architecture Dockerfile for production builds (AMD64/ARM64)
ARG USER_ID=1000
ARG GROUP_ID=1000

# Builder stage - need this since distroless has no package manager
FROM python:3.13-bookworm AS builder
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG TARGETPLATFORM
ARG TARGETARCH
ARG BUILDPLATFORM

ARG UV_LINK_MODE=copy

# Install all dependencies needed for building and runtime
# Note: ninja-build, libcairo2-dev, pkg-config are needed for building pycairo on ARM64
# (no pre-built wheel available, so it compiles from source)
RUN echo "Building for platform: ${TARGETPLATFORM:-unknown}, arch: ${TARGETARCH:-unknown}" && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates binutils \
        ninja-build libcairo2-dev pkg-config && \
    rm -rf /var/lib/apt/lists/*

# Create app directory
RUN mkdir -p /vss-agent && \
    chown ${USER_ID}:${GROUP_ID} /vss-agent

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set version for setuptools-scm
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VSS_AGENT=0.1.1
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0
ENV GIT_LFS_SKIP_SMUDGE=1

WORKDIR /vss-agent

# Copy project files
COPY --chown=${USER_ID}:${GROUP_ID} agent/pyproject.toml /vss-agent
COPY --chown=${USER_ID}:${GROUP_ID} agent/LICENSE-3rd-party.txt /vss-agent
COPY --chown=${USER_ID}:${GROUP_ID} agent/uv.lock /vss-agent
COPY --chown=${USER_ID}:${GROUP_ID} agent/src/vss_agents /vss-agent/src/vss_agents
COPY --chown=${USER_ID}:${GROUP_ID} agent/docker/cleanup_vulnerabilities.py /vss-agent/cleanup_vulnerabilities.py
COPY --chown=${USER_ID}:${GROUP_ID} agent/docker/verify_ffmpeg_tarball.py /vss-agent/verify_ffmpeg_tarball.py
COPY --chown=${USER_ID}:${GROUP_ID} agent/3rdparty/ffmpeg /vss-agent/third_party/ffmpeg

# Fail early if FFmpeg source tarball is missing or invalid (required for LGPL compliance)
RUN python3 /vss-agent/verify_ffmpeg_tarball.py

# Create virtual environment and install dependencies
# Use architecture-specific cache to avoid ARM64/AMD64 conflicts
RUN --mount=type=cache,id=uv_cache_${TARGETARCH},target=/root/.cache/uv,sharing=private \
    uv venv --seed .venv && \
    . .venv/bin/activate && \
    uv sync --frozen --no-dev --no-editable --link-mode copy

RUN uv pip uninstall setuptools
RUN rm -rf /vss-agent/.venv/lib/python3.13/site-packages/setuptools
RUN find /vss-agent/.venv -name "*.exe" -delete

# Cleanup to reduce image size
RUN find /vss-agent/.venv -type d -name "__pycache__" -exec rm -rf {} + && \
    find /vss-agent/.venv -type d -name "tests" -exec rm -rf {} + && \
    find /vss-agent/.venv -type f -name "*.pyc" -delete && \
    find /vss-agent/.venv -type f -name "*.pyo" -delete && \
    find /vss-agent/.venv -type f -name "*.a" -delete

# Remove PyTorch test binaries and unused components (~47 MB)
RUN find /vss-agent/.venv -type f -path "*/torch/bin/test_*" -delete && \
    find /vss-agent/.venv -type d -path "*/torch/test" -exec rm -rf {} + 2>/dev/null || true && \
    rm -f /vss-agent/.venv/lib/python3.13/site-packages/torch/bin/protoc* || true

# Remove imageio_ffmpeg binary if present (~76 MB) - not needed since we use opencv
RUN rm -rf /vss-agent/.venv/lib/python3.13/site-packages/imageio_ffmpeg 2>/dev/null || true

# Security patch stage - download patched OpenSSL libraries
# CVE-2025-69419, CVE-2025-69420, CVE-2025-15467: Upgrade libssl3 to 3.0.19-1~deb12u2
FROM debian:bookworm AS security-patches
ARG TARGETARCH

# Download the patched libssl3 package for the target architecture
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget ca-certificates && \
    mkdir -p /patches && \
    if [ "$TARGETARCH" = "amd64" ]; then \
        wget -O /patches/libssl3.deb http://deb.debian.org/debian/pool/main/o/openssl/libssl3_3.0.19-1~deb12u2_amd64.deb; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
        wget -O /patches/libssl3.deb http://deb.debian.org/debian/pool/main/o/openssl/libssl3_3.0.19-1~deb12u2_arm64.deb; \
    fi && \
    cd /patches && \
    dpkg-deb -x libssl3.deb /patches/libssl3-extracted && \
    rm -rf /var/lib/apt/lists/*

# Runtime stage - using NVIDIA distroless production image (supports AMD64 and ARM64)
FROM nvcr.io/nvidia/distroless/python:3.13-v3.1.3 AS runtime
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG TARGETPLATFORM
ARG TARGETARCH

# Copy cleanup script (as root to ensure proper permissions)
COPY --from=builder /vss-agent/cleanup_vulnerabilities.py /tmp/cleanup_vulnerabilities.py

# Ensure we're running as root (UID 0) for cleanup operations
# Distroless images don't have /etc/passwd, so use numeric UID
USER 0

# Remove unnecessary libraries using Python exec form (no shell needed)
RUN ["/usr/local/bin/python3", "/tmp/cleanup_vulnerabilities.py"]

# Remove the cleanup script after use
RUN ["/usr/local/bin/python3", "-c", "import os; os.remove('/tmp/cleanup_vulnerabilities.py')"]

# Remove the openssl CLI binary from the base image - the application only needs
# libssl3.so/libcrypto.so shared libraries, not the CLI tool. This eliminates Grype
# findings for CVE-2025-15467, CVE-2025-69419, CVE-2025-69420, CVE-2025-69421 etc.
RUN ["/usr/local/bin/python3", "-c", "import os; os.path.exists('/usr/bin/openssl') and os.remove('/usr/bin/openssl')"]

# Copy patched OpenSSL libraries to fix CVE-2025-69419, CVE-2025-69420, CVE-2025-15467
# These replace the base image libssl3 with 3.0.19-1~deb12u2
# Copy directly to architecture-specific paths where the runtime image expects them
COPY --from=security-patches /patches/libssl3-extracted/usr/lib/*-linux-gnu*/libssl.so.* /usr/lib/x86_64-linux-gnu/
COPY --from=security-patches /patches/libssl3-extracted/usr/lib/*-linux-gnu*/libssl.so.* /usr/lib/aarch64-linux-gnu/
COPY --from=security-patches /patches/libssl3-extracted/usr/lib/*-linux-gnu*/libcrypto.so.* /usr/lib/x86_64-linux-gnu/
COPY --from=security-patches /patches/libssl3-extracted/usr/lib/*-linux-gnu*/libcrypto.so.* /usr/lib/aarch64-linux-gnu/

# Copy application without any dev needs
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} /vss-agent/.venv /vss-agent/.venv
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} /vss-agent/third_party/ffmpeg /vss-agent/third_party/ffmpeg
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} /vss-agent/LICENSE-3rd-party.txt /vss-agent/LICENSE-3rd-party.txt
FROM runtime AS agent-runtime
ARG TARGETARCH

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/usr/local/bin:/vss-agent/.venv/bin:$PATH"
# Set architecture-specific library paths based on Debian multiarch
# Note: We include both possible paths for compatibility
ENV LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH}"
ENV FONTCONFIG_PATH="/etc/fonts"
ENV XDG_DATA_DIRS="/usr/share"

# Default config (will be overridden by docker-compose env vars)
ENV HOST="0.0.0.0"
ENV PORT="8000"

WORKDIR /vss-agent

# Switch to non-root user
USER ${USER_ID}:${GROUP_ID}

# Default CMD can be overridden by docker-compose
ENTRYPOINT ["/vss-agent/.venv/bin/nat"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]