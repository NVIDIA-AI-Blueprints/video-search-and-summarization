from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    # Kafka.
    kafka_bootstrap_servers: str = "localhost:9092"
    # Work items fanned out by the backend (slice i -> partition i).
    eval_slices_topic: str = "eval-slices"
    # Completion notifications, keyed by eval_id (all a job's slices -> one partition).
    eval_results_topic: str = "eval-results"
    eval_slices_group: str = "eval-slices-consumer"
    eval_summary_group: str = "eval-summary-consumer"

    # Elasticsearch (holds the shared PIT the backend opened).
    es_url: str = "http://localhost:9200"
    # PIT keep-alive fallback (work items normally carry their own keep_alive).
    pit_keep_alive: str = "10m"

    # Object store (SeaweedFS S3) — where manifests and eval.json are written.
    aws_endpoint: str = "http://seaweedfs-s3:8333"
    aws_access_key_id: str = "seaweedfs"
    aws_secret_access_key: str = "seaweedfs"
    aws_bucket: str = "aoi-samples"
    # Key prefix; layout <prefix>/<eval_id>/... (must match backend-api).
    eval_prefix: str = "evals"

    @field_validator("eval_prefix")
    @classmethod
    def _validate_eval_prefix(cls, v: str) -> str:
        v = v.strip().strip("/")
        if not v:
            raise ValueError("eval_prefix must be non-empty (no leading/trailing slash)")
        return v

    # search_after page size for the slice export loop (design §7: 1k-5k).
    eval_page_size: int = 1000

    # In-place retry for a failed message so the long-running worker never has to
    # exit/restart. Handlers are idempotent (marker PUT keyed by slice_id;
    # try_finalize guarded by eval.json), so retrying is safe.
    handler_max_retries: int = 5
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0

    log_level: str = "INFO"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
