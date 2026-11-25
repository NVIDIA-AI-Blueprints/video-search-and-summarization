FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Shared library resolved by the `../deft_common` path dependency; provided as a
# named build context (see build.additional_contexts in the compose file).
COPY --from=deft_common . /deft_common/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev


FROM python:3.14-slim

RUN useradd --create-home appuser

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY . ./eval_set_worker

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Default to the slice consumer; the summary service overrides `command`
# in compose to run eval_set_worker.summary_service instead.
CMD ["python", "-m", "eval_set_worker.slice_service"]
