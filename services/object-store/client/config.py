from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    # AOI #2 inference service.
    aoi2_base_url: str = "http://localhost:5000"
    aoi2_batch_path: str = "/v1/predict:batch"
    aoi2_ready_path: str = "/v1/ready"

    # Kafka (Message Bus).
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "aoi-inference-results"

    # Batching / parallelism.
    batch_size: int = 16
    semaphore_permits: int = 48
    intake_queue_size: int = 64

    # Crop encoding format sent to AOI #2 ("JPEG" or "PNG").
    crop_format: str = "JPEG"

    # Limits & timeouts.
    max_request_bytes: int = 50 * 1024 * 1024
    inference_timeout_s: float = 60.0
    board_deadline_s: float = 300.0

    # Retry.
    max_retries: int = 3
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    retry_jitter: bool = True

    # Kafka outage handling.
    kafka_buffer_max: int = 1000
    kafka_replay_interval_s: float = 5.0

    log_level: str = "INFO"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
