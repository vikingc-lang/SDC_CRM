"""Application settings, loaded from environment variables (see .env.example)."""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "relate [R]"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://relate_user:relate_secure_password@localhost:5432/relate_crm"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "super_secret_jwt_key_change_in_production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12

    cors_origins: str = "http://localhost:3000"

    # --- Intelligence layer -------------------------------------------------
    # 'ollama' (private, local) | 'aws_bedrock' (Claude in your VPC) |
    # 'anthropic' (Claude API) | 'heuristic' (deterministic, offline).
    # Any provider failure degrades gracefully to 'heuristic'.
    llm_provider: Literal["ollama", "aws_bedrock", "anthropic", "heuristic"] = "heuristic"
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-opus-5"
    bedrock_embed_model_id: str = "amazon.titan-embed-text-v1"
    anthropic_model: str = "claude-opus-5"
    llm_timeout_seconds: float = 60.0

    # 'hash' (offline feature-hashing) | 'ollama' | 'aws_bedrock'
    embedding_provider: Literal["hash", "ollama", "aws_bedrock"] = "hash"
    embedding_dim: int = 1536

    # Run background jobs through Celery (requires a worker). When false,
    # jobs run in-process via FastAPI background tasks.
    use_celery: bool = False
    # Celery tasks each run in a fresh event loop, so workers must not pool
    # asyncpg connections across tasks.
    db_null_pool: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
