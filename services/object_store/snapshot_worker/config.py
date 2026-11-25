from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    # Kafka.
    kafka_bootstrap_servers: str = "localhost:9092"
    snapshot_slices_topic: str = "snapshot-slices"
    snapshot_summary_topic: str = "snapshot-summary"
    slices_group_id: str = "snapshot-slices-consumers"
    summary_group_id: str = "snapshot-summary-consumers"

    # Elasticsearch (holds the shared PIT the BE opened).
    es_url: str = "http://localhost:9200"

    # Object store (SeaweedFS S3) — where manifests and snapshot.json are written.
    aws_endpoint: str = "http://seaweedfs-s3:8333"
    aws_access_key_id: str = "seaweedfs"
    aws_secret_access_key: str = "seaweedfs"
    aws_bucket: str = "aoi-samples"
    snapshot_prefix: str = "snapshots"

    # search_after page size (design §7: 1,000-5,000).
    page_size: int = 1000

    # Per-slice retry on transient errors before the slice is marked failed.
    max_retries: int = 3
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    retry_jitter: bool = True

    log_level: str = "INFO"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
