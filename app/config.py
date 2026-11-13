import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    cache_enabled: bool
    cache_ttl_seconds: int
    app_version: str
    # Used to simulate a broken release: readiness fails, so Kubernetes
    # never routes traffic to the new pods and the rollout gets rolled back.
    fail_readiness: bool


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./local.db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        cache_enabled=_bool("CACHE_ENABLED", True),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "60")),
        app_version=os.getenv("APP_VERSION", "dev"),
        fail_readiness=_bool("FAIL_READINESS", False),
    )
