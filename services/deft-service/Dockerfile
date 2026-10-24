# DEFT service image.
#
# Real execution mode launches TAO containers as SIBLINGS via the host's
# docker daemon, so deployments must mount the docker socket (compose:
# -v /var/run/docker.sock:/var/run/docker.sock) or run the service directly
# on the host / in a pod with a docker sidecar. The service itself needs no
# GPU — only the stage containers it launches do.
FROM python:3.12-slim

# docker CLI for the local-docker backend (talks to the mounted socket)
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/deft-service
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY openapi.json ./
COPY app ./app

ENV DEFT_EXECUTION_MODE=simulate \
    DEFT_BACKEND_TYPE=docker \
    DEFT_DB_PATH=/srv/deft-service/data/deft_service.db
VOLUME ["/srv/deft-service/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
