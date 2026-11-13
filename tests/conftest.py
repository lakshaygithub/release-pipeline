import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import make_engine
from app.main import create_app


def make_settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite:///:memory:",
        redis_url="redis://unused",
        cache_enabled=True,
        cache_ttl_seconds=60,
        app_version="test",
        fail_readiness=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture
def make_client(redis_client):
    def _make(redis=redis_client, **overrides):
        settings = make_settings(**overrides)
        app = create_app(settings, engine=make_engine(settings.database_url), redis_client=redis)
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c
