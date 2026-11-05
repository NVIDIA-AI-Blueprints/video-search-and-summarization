FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/al_archiver

COPY client/ /app/client/
COPY al_archiver/pyproject.toml al_archiver/uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY al_archiver/ ./

RUN uv sync --frozen --no-dev


FROM python:3.14-slim

RUN useradd --create-home appuser

WORKDIR /app

COPY --from=builder /app/al_archiver/.venv .venv
COPY --from=builder /app/al_archiver al_archiver

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "al_archiver.application:app", "--host", "0.0.0.0", "--port", "8000"]
