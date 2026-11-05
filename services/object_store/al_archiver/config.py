from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    # Kafka: consume the AOI Client's inference results.
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "aoi-inference-results"
    kafka_group_id: str = "al-archiver"

    # Backend (Object Store-ELK BE): where forwarded results are sent.
    be_base_url: str = "http://backend-api:8000"
    be_inference_result_path: str = "/api/inference-results"

    # Forwarding to the BE.
    forward_timeout_s: float = 30.0
    max_retries: int = 5
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    retry_jitter: bool = True

    log_level: str = "INFO"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
