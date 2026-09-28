"""Application settings, loaded from environment variables (see .env.example)."""
import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Cirra"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://cirra_user:cirra_secure_password@localhost:5432/cirra_crm"
    redis_url: str = "redis://localhost:6379/0"

    # Signs sign-in tokens (and, without DATA_ENCRYPTION_KEY, derives the key for stored credentials). No default:
    # the container entrypoint generates one per install; see enforce_secure_settings().
    jwt_secret: str = ""
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

    # System mail (scheduled report emails). Unset host: deliveries are in-app notifications only.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "cirra@localhost"
    smtp_starttls: bool = True

    # API rate limits, requests per minute (see core/ratelimit.py). Counted in Redis across replicas.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 600            # a signed-in user
    rate_limit_api_key_per_minute: int = 300    # an API key
    rate_limit_anonymous_per_minute: int = 120  # unauthenticated calls to the API, per IP
    rate_limit_login_per_minute: int = 60       # sign-in and two-factor attempts, per IP (per-account lockout is separate)
    rate_limit_public_per_minute: int = 120     # public forms, e-signature, CSAT, unsubscribe, inbound email, per IP
    rate_limit_tracking_per_minute: int = 1200  # email open pixels and click-throughs, per IP
    log_format: Literal["text", "json"] = "text"

    # Public base URL of this API (email open/click tracking links point here).
    public_api_url: str = "http://localhost:8000"
    # Email-to-case: shared secret for POST /api/v1/inbound/email (unset = endpoint disabled), and an
    # optional support mailbox polled over IMAP every two minutes.
    inbound_email_secret: str | None = None
    support_imap_host: str | None = None
    support_imap_port: int = 993
    support_imap_user: str | None = None
    support_imap_password: str | None = None

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


# Secrets that have been published (old defaults, examples) or are trivially guessable.
KNOWN_INSECURE_SECRETS = frozenset({
    "super_secret_jwt_key_change_in_production", "change_me", "change-me", "changeme", "secret", "jwt_secret",
    "your-secret-key", "cirra", "password",
})
DEV_ENVIRONMENTS = ("development", "dev", "local", "test")
MIN_SECRET_LENGTH = 32


def security_problems(s: "Settings") -> list[str]:
    """What makes this configuration unsafe for real data."""
    problems = []
    if not s.jwt_secret or s.jwt_secret.strip().lower() in KNOWN_INSECURE_SECRETS or len(s.jwt_secret) < MIN_SECRET_LENGTH:
        problems.append(f"JWT_SECRET is missing, a published default or shorter than {MIN_SECRET_LENGTH} characters, "
                        "so anyone could forge sign-in tokens")
    if s.data_encryption_key is not None and (s.data_encryption_key.strip().lower() in KNOWN_INSECURE_SECRETS
                                              or len(s.data_encryption_key) < MIN_SECRET_LENGTH):
        problems.append(f"DATA_ENCRYPTION_KEY is a published default or shorter than {MIN_SECRET_LENGTH} characters")
    return problems


def enforce_secure_settings(s: "Settings") -> None:
    """Called at API and worker start. Outside development an unsafe secret stops the process; in development it
    is allowed with a warning (an empty secret gets a random one for this process only)."""
    log = logging.getLogger("cirra.security")
    problems = security_problems(s)
    if not problems:
        if s.data_encryption_key is None and s.environment.lower() not in DEV_ENVIRONMENTS:
            log.warning("DATA_ENCRYPTION_KEY is not set: stored mailbox credentials use a key derived from JWT_SECRET. "
                        "Set a separate DATA_ENCRYPTION_KEY.")
        return
    if s.environment.lower() in DEV_ENVIRONMENTS:
        if not s.jwt_secret:
            s.jwt_secret = secrets.token_urlsafe(48)  # sessions end when this process restarts
        log.warning("INSECURE CONFIGURATION, allowed only because ENVIRONMENT=%s: %s. Never use this setup for real data.",
                    s.environment, "; ".join(problems))
        return
    raise RuntimeError("Refusing to start: " + "; ".join(problems) + ". Set JWT_SECRET (and DATA_ENCRYPTION_KEY) to long "
                       "random values, e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`, or leave them "
                       "empty in docker compose to have them generated on first start. For a local demo only, set "
                       "ENVIRONMENT=development.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
