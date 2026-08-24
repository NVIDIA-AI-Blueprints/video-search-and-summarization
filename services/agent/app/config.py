from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    artifacts_bucket: str = "ava-artifacts-dev"
    bedrock_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    max_segments_per_search: int = 8
    agent_timeout_seconds: float = 110.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
