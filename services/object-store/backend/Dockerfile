FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Path dependencies resolved relative to WORKDIR, each supplied as a named build
# context (see build.additional_contexts in the compose file). al_archiver brings
# the Smart City frame consumer the app runs in-process; client comes with it,
# since al_archiver declares it.
COPY --from=deft_common . /deft_common/
COPY --from=al_archiver . /al_archiver/
COPY --from=client . /client/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY . ./backend

RUN uv sync --frozen --no-dev


FROM python:3.14-slim

# jpegtran, djpeg and cjpeg for SC_FRAME_COMPRESSION (backend/api/sc_compress.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg-turbo-progs \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY --from=builder /app/backend backend

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "backend.application:app", "--host", "0.0.0.0", "--port", "8000"]
