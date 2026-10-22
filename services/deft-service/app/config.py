"""Service configuration, sourced from environment variables where a real
deployment would need to vary them (compute backend, storage root, GPU
ceiling) and hardcoded where it wouldn't. Kept in one place so the rest of
the app never reads os.environ directly.
"""

import os

SERVICE_VERSION = "0.2.0"

SUPPORTED_BACKEND_TYPES = ("docker", "kfp", "slurm", "brev")
BACKEND_TYPE = os.environ.get("DEFT_BACKEND_TYPE", "docker")
if BACKEND_TYPE not in SUPPORTED_BACKEND_TYPES:
    raise ValueError(f"DEFT_BACKEND_TYPE={BACKEND_TYPE!r} must be one of {SUPPORTED_BACKEND_TYPES}")

STORAGE_ROOT = os.environ.get("DEFT_STORAGE_ROOT", "file:///data/deft")

# Where s3:// references from fine-tune requests are downloaded before the
# docker backend runs (see app/execution/materialize.py). Per-run subdirs.
MATERIALIZE_ROOT = os.environ.get("DEFT_MATERIALIZE_ROOT", "/tmp/deft_materialize")
# Optional safety boundary for local workspaces used by the Docker backend.
# When set, real-mode requests may only use this directory or its descendants.
# Containerized deployments should identity-mount this path so it resolves to
# the same absolute location for this service and the host Docker daemon.
ALLOWED_WORKSPACE_ROOT = os.environ.get("DEFT_ALLOWED_WORKSPACE_ROOT")

# --- Credential-free READ materialize via the Storage Backend presign endpoint ---
# When DEFT_MATERIALIZE_PRESIGN_URL is set AND the fine-tune request carries no
# storage_access (temporary credentials), deft resolves its s3:// references to
# presigned GET URLs by calling the Storage Backend (POST /v1/get_presigned_urls)
# and downloads over http(s) — so deft needs NO object-store credentials. This is
# the read-side twin of DEFT_PUBLISH_PRESIGN_URL. When unset, s3:// references fall
# back to the boto3 path (storage_access temp creds, or the default cred chain).
MATERIALIZE_PRESIGN_URL = os.environ.get("DEFT_MATERIALIZE_PRESIGN_URL")
# Optional bearer for the presign REQUEST.
MATERIALIZE_PRESIGN_TOKEN = os.environ.get("DEFT_MATERIALIZE_PRESIGN_TOKEN")
# Storage Backend base URL (GET /v1/snapshot/{id}) to enumerate the objects under
# an s3:// snapshot prefix in presign mode. Defaults to the presign URL's origin.
MATERIALIZE_STORAGE_URL = os.environ.get("DEFT_MATERIALIZE_STORAGE_URL")
# Presigned-GET lifetime requested from the Storage Backend.
MATERIALIZE_PRESIGN_EXPIRES_S = int(os.environ.get("DEFT_MATERIALIZE_PRESIGN_EXPIRES_S", "3600"))

# s3://bucket/prefix to PUBLISH real-mode run artifacts under (best checkpoint
# + training spec, per run id). Unset -> results keep file:// host paths.
PUBLISH_PREFIX = os.environ.get("DEFT_PUBLISH_PREFIX")

# --- Credential-free publish via S3 presigned POST (preferred write-back) -----
# When DEFT_PUBLISH_PRESIGN_URL is set, real-mode artifacts are uploaded with an
# S3 *presigned POST* obtained from the Storage Backend at that URL, instead of
# the boto3 DEFT_PUBLISH_PREFIX path — so DEFT needs no object-store credentials
# (symmetric with the presigned-GET read path in materialize.py). Takes
# precedence over DEFT_PUBLISH_PREFIX when both are set.
#
#   Request  (POST application/json): {"content_type", "folder", "file_name"}
#   Response (application/json)      : {"url", "fields"[, "uri" | "bucket"]}
#
# DEFT then streams the file to `url` as a multipart/form-data POST carrying
# `fields` + the file part, streamed via httpx (bounded memory).
PUBLISH_PRESIGN_URL = os.environ.get("DEFT_PUBLISH_PRESIGN_URL")
# The presigned objects land at "<server upload prefix>/<run_id>/<file_name>":
# the Storage Backend owns the prefix and accepts a single-segment ``folder``
# (pattern ^[A-Za-z0-9_-]+$), so DEFT sends only the run id (a uuid4 string) as
# the folder — no client-side base prefix.
# Optional bearer token for the presign REQUEST to the Storage Backend.
PUBLISH_PRESIGN_TOKEN = os.environ.get("DEFT_PUBLISH_PRESIGN_TOKEN")
# Content-Type DEFT signs + uploads artifacts with; must match the POST policy
# the Storage Backend builds (SeaweedFS is strict about Content-Type on
# presigned uploads — it must be identical at sign time and upload time).
PUBLISH_CONTENT_TYPE = os.environ.get("DEFT_PUBLISH_CONTENT_TYPE", "application/octet-stream")
PUBLISH_PRESIGN_TIMEOUT_S = int(os.environ.get("DEFT_PUBLISH_PRESIGN_TIMEOUT_S", "60"))
PUBLISH_UPLOAD_TIMEOUT_S = int(os.environ.get("DEFT_PUBLISH_UPLOAD_TIMEOUT_S", "600"))

MAX_NUM_GPUS = int(os.environ.get("DEFT_MAX_NUM_GPUS", "64"))

# ---------------------------------------------------------------------------
# Training-label policy
#
# An AOI component carries two labels: the station's preliminary
# `aoiDefectInfo.defectType`, and `confirmed_label` (PASS | DEFECT) — the
# confirmation the AOI station sends at ingest. They can disagree, and the
# disagreement is the point: a component the station flagged but that was
# confirmed PASS is a FALSE ALARM, which is exactly what the model is being
# trained to stop producing (the KPI is FAR).
#
# `confirmed_label` is always authoritative on the PASS/DEFECT question. This
# setting only decides what to do with a component that has NO confirmation:
#
#   confirmed_else_machine (default) — fall back to the station's defectType.
#       Keeps historical records usable (`confirmed_label` postdates them) and
#       is the safe default while station rollout is incomplete.
#   confirmed_only — exclude the component from the pool entirely. Correct once
#       every station confirms, since it guarantees no unverified label is ever
#       trained on. Will shrink or empty the pool if confirmations are missing.
# ---------------------------------------------------------------------------
LABEL_POLICY = os.environ.get("DEFT_LABEL_POLICY", "confirmed_else_machine")
if LABEL_POLICY not in ("confirmed_else_machine", "confirmed_only"):
    raise ValueError(
        f"DEFT_LABEL_POLICY={LABEL_POLICY!r} must be "
        "'confirmed_else_machine' or 'confirmed_only'"
    )

# ---------------------------------------------------------------------------
# Execution mode
#
#   simulate (default) — walk the workflow plan with short sleeps and
#       synthetic metrics. Exercises the API contract; safe everywhere
#       (CI, laptops, no GPU). This is the pre-0.2 behavior.
#   real — execute the DEFT AOI pipeline for real on the selected backend.
#       Implemented for workflow `aoi_active_learning` on the `docker`
#       backend (TAO VisualChangeNet train/inference containers, SigLIP
#       k-NN mining, KPI threshold sweep). `tmm_only` real execution is
#       not implemented and is rejected at submission time.
# ---------------------------------------------------------------------------
EXECUTION_MODE = os.environ.get("DEFT_EXECUTION_MODE", "simulate")
if EXECUTION_MODE not in ("simulate", "real"):
    raise ValueError(f"DEFT_EXECUTION_MODE={EXECUTION_MODE!r} must be 'simulate' or 'real'")

# SQLite persistence (survives restarts; swap store.py for Postgres at scale).
DB_PATH = os.environ.get("DEFT_DB_PATH", "deft_service.db")

# Auth mode: "trusted" (demo — bearer token content IS the identity) or
# "static" (bearer token must match a configured API key).
# DEFT_API_KEYS format: "<token>:<subject>[,<token>:<subject>...]"; prefix the
# subject with "admin:" to grant the deft:admin scope.
AUTH_MODE = os.environ.get("DEFT_AUTH_MODE", "trusted")
if AUTH_MODE not in ("trusted", "static"):
    raise ValueError(f"DEFT_AUTH_MODE={AUTH_MODE!r} must be 'trusted' or 'static'")
API_KEYS_SPEC = os.environ.get("DEFT_API_KEYS", "")

# Real-execution knobs (docker backend).
TAO_PYT_IMAGE = os.environ.get("DEFT_TAO_PYT_IMAGE", "nvcr.io/nvidia/tao/tao-toolkit:6.26.3-pyt")
SIGLIP_MODEL = os.environ.get("DEFT_SIGLIP_MODEL", "google/siglip-base-patch16-224")
GPU_SLOTS = int(os.environ.get("DEFT_GPU_SLOTS", "1"))          # concurrent GPU containers
STAGE_TIMEOUT_S = int(os.environ.get("DEFT_STAGE_TIMEOUT_S", "14400"))  # per container run
CONTAINER_WORKSPACE = "/data/workspace"                          # mount point inside containers

# Kept small so the bundled run simulation finishes in seconds instead of
# hours — simulation exercises the API contract, not real GPU training.
SIMULATED_STEP_SECONDS = 0.4
MAX_SIMULATED_ITERATIONS = 3
