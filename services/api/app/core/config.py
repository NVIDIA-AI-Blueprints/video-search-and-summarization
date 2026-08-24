from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "video-analysis-api"
    environment: str = "dev"

    aws_region: str = "us-east-1"
    media_bucket: str = "ava-media-dev"
    artifacts_bucket: str = "ava-artifacts-dev"
    videos_table: str = "ava-videos"
    users_table: str = "ava-users"
    audit_table: str = "ava-audit"

    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""

    agent_service_url: str = "http://localhost:8100"
    agent_timeout_seconds: float = 120.0

    allowed_origins: list[str] = ["http://localhost:3000"]

    @property
    def cognito_jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"
            f"{self.cognito_user_pool_id}/.well-known/jwks.json"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
