from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    aws_endpoint: str
    # Host external clients use to download presigned URLs; default = aws_endpoint.
    aws_public_endpoint: str | None = None
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_bucket: str
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "aoi-metadata"
    max_images_per_request: int = 10
    max_request_bytes: int = 10 * 1024 * 1024

    # Smart City / ITS archival. DeepStream publishes selected frames to
    # sc_frames_topic with the image inline; the consumer writes the image to
    # the object store and republishes metadata-only records to
    # sc_metadata_topic, where the sc-ingest Logstash pipeline indexes them.
    # Off by default: the backend is shared with AOI, and a deployment that does
    # not run Smart City should not grow a consumer for it.
    sc_consumer_enabled: bool = False
    sc_frames_topic: str = "selected-frames"
    sc_metadata_topic: str = "sc-metadata"
    sc_consumer_group: str = "sc-archiver"
    # A Selected Image Schema record carries the frame as base64, so it runs to
    # roughly 1.37x the JPEG and the 1 MiB default would refuse the larger ones.
    # Keep in step with the producer cap and the broker message.max.bytes.
    sc_max_partition_fetch_bytes: int = 4 * 1024 * 1024
    # Object-store prefix for archived ITS frames, mirroring the AOI deft-aoi/
    # prefix. Configurable because the naming is not settled with the ITS team.
    its_object_prefix: str = "deft-its"
    # Re-encoding of archived ITS JPEG frames before they are stored (sc_compress).
    # off: store the bytes as received. lossless: jpegtran -optimize -progressive,
    # pixel-identical. lossy: re-encode at sc_frame_jpeg_quality. KPI frames are
    # never re-encoded. Needs libjpeg-turbo's jpegtran/djpeg/cjpeg on PATH.
    sc_frame_compression: Literal["off", "lossless", "lossy"] = "off"
    sc_frame_jpeg_quality: int = Field(default=85, ge=50, le=95)

    # Retention for archived ITS frames (sc_gc). Each pass hides the catalog
    # documents ingested before the cutoff from new snapshots (expiredAt), and
    # deletes documents hidden for longer than sc_gc_hold_s together with their
    # objects, then asks the SeaweedFS master to vacuum so the space is returned
    # to the disk. Off by default; run it on one backend replica only.
    sc_gc_enabled: bool = False
    # Report what a pass would do without changing anything.
    sc_gc_dry_run: bool = False
    sc_gc_interval_s: int = Field(default=3600, gt=0)
    sc_gc_retention_days: float = Field(default=14.0, gt=0)
    # Only hide frames ingested before the latest ITS snapshot was requested.
    sc_gc_require_snapshot: bool = True
    # How long a hidden frame stays readable. Every snapshot keeps its frames at
    # least this long after it was taken, so this must exceed the time from a
    # snapshot request to the end of the fine-tuning run that reads it.
    sc_gc_hold_s: int = Field(default=48 * 3600, ge=0)
    # Also delete ITS snapshot directories (manifests) older than the hold,
    # keeping the latest ITS snapshot.
    sc_gc_prune_snapshots: bool = True
    sc_gc_batch_size: int = Field(default=500, gt=0, le=1000)
    sc_gc_max_deletes_per_pass: int = Field(default=100_000, gt=0)
    # Also delete objects under its_object_prefix that no catalog document
    # points to and that are older than the grace period. Off by default: only
    # failed deletes and documents removed outside this pass leave orphans.
    sc_gc_orphan_sweep: bool = False
    sc_gc_orphan_grace_s: int = Field(default=24 * 3600, ge=0)
    # Abort the sweep when more than this share of a listing page is
    # unreferenced; that means the catalog does not describe these objects.
    sc_gc_orphan_max_ratio: float = Field(default=0.2, gt=0, le=1)
    # SeaweedFS master for /vol/vacuum; empty skips the vacuum.
    seaweedfs_master_url: str = ""
    sc_gc_vacuum_garbage_threshold: float = Field(default=0.1, gt=0, lt=1)
    sc_gc_vacuum_timeout_s: float = Field(default=1800.0, gt=0)

    # Tag archived ITS frames, except KPI-set ones, with deft-retention=its-frame
    # so a bucket lifecycle rule can expire them (sc_lifecycle). The tag alone
    # changes nothing.
    sc_frame_tagging: bool = True
    # Keep a bucket lifecycle rule that expires tagged ITS frames once they are
    # older than retention + hold + 1 day, a backstop for sc_gc, which normally
    # deletes them first. Needs sc_gc_enabled, SEAWEEDFS_MASTER_URL, SeaweedFS
    # 4.24 or later and a `weed worker -jobType=s3_lifecycle`. Off removes it.
    sc_lifecycle_enabled: bool = False

    @field_validator("its_object_prefix")
    @classmethod
    def _validate_its_object_prefix(cls, v: str) -> str:
        # Keys are "<prefix>/<digest>/<frameId>.<ext>". A stray slash would put
        # "//" in every key, which SeaweedFS lists collapsed, so no catalog path
        # would match a listed key.
        v = v.strip().strip("/")
        if not v:
            raise ValueError("its_object_prefix must be non-empty (no leading/trailing slash)")
        return v

    # Workflow 2 — outbound trigger to AOI Client
    inference_enabled: bool = False
    aoi_client_base_url: str = "http://localhost:8010"
    aoi_client_timeout_s: float = 10.0
    aoi_client_max_retries: int = 3
    aoi_client_retry_base_delay_s: float = 1.0
    aoi_client_retry_max_delay_s: float = 30.0
    dev_use_sample_as_golden_ref: bool = False

    # Workflow 2 — inbound inference results + ES upsert (AsyncElasticsearch)
    elasticsearch_index_pattern: str = "aoi_*"

    # Catalog snapshot + shared ES client (AsyncElasticsearch: PIT + WF2 search/update)
    es_url: str = Field(
        default="http://localhost:9200",
        validation_alias=AliasChoices("ES_URL", "ELASTICSEARCH_URL"),
    )
    es_index: str = "aoi_*"
    # Smart City / ITS frames are indexed apart from AOI inspections; the
    # catalog segment of the request path selects between the two patterns.
    sc_es_index: str = "sc_*"
    pit_keep_alive: str = "10m"
    snapshot_slices_topic: str = "snapshot-slices"
    # N = slice count = snapshot-slices partition count (env: SNAPSHOT_PARTITIONS).
    snapshot_partitions: int = 3
    # Object-store prefix for snapshots; must match the worker's SNAPSHOT_PREFIX.
    snapshot_prefix: str = "snapshots"

    # KPI set — same plan+fan-out shape as snapshots (kpi-slices / kpi-summary).
    kpi_slices_topic: str = "kpi-slices"
    # N = slice count = kpi-slices partition count (env: KPI_PARTITIONS).
    kpi_partitions: int = 3
    # Object-store prefix; layout <prefix>/<kpi_id>/... must match the KPI worker.
    kpi_prefix: str = "kpi"

    presign_expires_s: int = 3600
    max_presign_urls_per_request: int = 50
    # Track 11 — Obj Store / Eval set (API plane only; the data-plane consumers
    # live in eval_set_worker/ with their own config). The API reuses the snapshot
    # planning (single shared PIT + sliced search_after) and fans out N slice
    # work items + a completion topic keyed by eval_id.
    # eval_slices_topic: N partitions, work items, slice i -> partition i.
    # eval_results_topic: completion notifications, keyed by eval_id.
    eval_slices_topic: str = "eval-slices"
    eval_results_topic: str = "eval-results"
    # N = slice count = eval-slices partition count (env: EVAL_PARTITIONS).
    eval_partitions: int = 3
    # Object-store prefix under the bucket; layout <prefix>/<eval_id>/...
    eval_prefix: str = "evals"

    @field_validator("eval_prefix")
    @classmethod
    def _validate_eval_prefix(cls, v: str) -> str:
        # Feeds the presign allow-list — an empty or "/" value would widen it to
        # every key, so reject those and normalise any stray slashes.
        v = v.strip().strip("/")
        if not v:
            raise ValueError("eval_prefix must be non-empty (no leading/trailing slash)")
        return v
    # Max upload size for presigned POST (enforced via content-length-range).
    presign_post_max_bytes: int = 5 * 1024 * 1024 * 1024  # 5 GB
    upload_folder: str = "upload"

    model_config = {"env_file": str(_ENV_FILE)}


settings = Settings()
