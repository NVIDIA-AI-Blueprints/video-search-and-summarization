# VST BDD Test Suite Docker Image
# Provides a ready-to-run testing environment with all dependencies

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.7.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    FORCE_COLOR=1 \
    PY_COLORS=1

# Add Poetry to PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Video processing
    ffmpeg \
    mediainfo \
    # Image processing
    jpeginfo \
    # Network tools
    curl \
    wget \
    # Build essentials for some Python packages
    gcc \
    g++ \
    make \
    # SSL certificates
    ca-certificates \
    # For WebRTC (libav dependencies)
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev \
    libopus-dev \
    libvpx-dev \
    pkg-config \
    # Other utilities
    git \
    # Docker CLI for container monitoring
    apt-transport-https \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - \
    && poetry --version

# Set working directory
WORKDIR /app

# Copy the dependency manifest first (for better caching).
#
# poetry.lock is deliberately NOT committed to the repo and NOT copied here:
# this is a dev-only test harness that ships nothing, but a checked-in lock file
# is picked up by the OSS/nspect source scan as a dependency manifest and its
# pinned transitive versions get reported as findings. nspect excludes by
# directory only, and we do not want to exclude all of test/. So the lock is
# resolved at build time instead (see .gitignore).
#
# The resolved lock IS written into the image at /app/poetry.lock, so a given
# published bdd_tests:<TAG> has one fixed dependency set -- but two builds of
# the same source can differ. The constraints in pyproject.toml (including the
# security floors) are the only thing bounding resolution; keep them tight.
COPY pyproject.toml ./

# Resolve and install Python dependencies (without dev dependencies).
# poetry show records the resolved set in the build log and in the image, since
# there is no committed lock file to diff against.
RUN poetry lock \
    && poetry install --no-root --only main \
    && poetry show --only main > /app/resolved-deps.txt

# Copy the entire project. This also bakes test_videos/ (the sample clips) into
# the image -- .dockerignore explicitly un-ignores test_videos/. Unlike tests/,
# features/, scripts/, data/, conftest.py, it is NOT bind-mounted at runtime, so
# the baked /app/test_videos copies are always available. The BDD session
# prerequisite (conftest.py) uploads them to NVStreamer when it has no streams.
COPY . .

# Clean any cached Python bytecode with absolute paths
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Create reports directory structure
RUN mkdir -p reports/stats reports/latency reports/performance reports/unit_tests

# Make unit test runner script executable
RUN chmod +x scripts/run_unit_tests.sh

# Install the project itself
RUN poetry install --only-root

# Set up entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Default command runs all tests
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["pytest", "-v"]
