"""Application settings, loaded from environment variables (see .env.example)."""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Cirra"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://cirra_user:cirra_secure_password@localhost:5432/cirra_crm"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "super_secret_jwt_key_change_in_production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12

    cors_origins: str = "http://localhost:3000"
    public_web_url: str = "http://localhost:3000"

    # Encrypts stored secrets (mailbox passwords). Derived from JWT_SECRET when unset.
    data_encryption_key: str | None = None
    # File storage for attachments, generated PDFs and collateral (a mounted volume in production).
    storage_dir: str = "./storage"
    max_upload_mb: int = 25

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

    # Voice: 'whisper_asr' (self-hosted service) | 'faster_whisper' (in-process) | 'disabled'
    transcription_provider: Literal["whisper_asr", "faster_whisper", "disabled"] = "disabled"
    whisper_endpoint: str = "http://localhost:9000"
    whisper_model: str = "base"

    # ERP / back-office: 'demo' | 'file' (JSON exchange folder) | 'rest' (middleware API) | 'disabled'
    erp_connector: Literal["demo", "file", "rest", "disabled"] = "demo"
    erp_exchange_dir: str = "./erp-exchange"
    erp_rest_url: str | None = None
    erp_rest_token: str | None = None
    # JSON map of SDC module -> webhook URL for ecosystem event delivery, e.g. {"yield": "http://yield/api/events"}
    ecosystem_webhooks: str = "{}"

    # Lead enrichment: 'internal' (existing accounts/leads, no network) | 'rest' (provider or MDM proxy) | 'disabled'
    enrichment_provider: Literal["internal", "rest", "disabled"] = "internal"
    enrichment_rest_url: str | None = None
    enrichment_rest_token: str | None = None

    # E-signature: 'builtin' (default, no egress) or an external provider for execution packets
    esign_provider: Literal["builtin", "docusign", "adobe_sign"] = "builtin"
    esign_api_url: str | None = None          # e.g. https://demo.docusign.net/restapi/v2.1/accounts/<id>
    esign_api_token: str | None = None
    esign_webhook_secret: str | None = None   # shared secret expected in the provider callback header

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
