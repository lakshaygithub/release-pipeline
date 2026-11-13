from tests.test_items import BrokenRedis


def test_liveness(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ready_when_database_is_up(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_bad_release_reports_not_ready(make_client):
    with make_client(fail_readiness=True) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        # Liveness stays green: the pod should be kept out of traffic, not restarted.
        assert client.get("/healthz").status_code == 200


def test_redis_outage_does_not_fail_readiness(make_client):
    with make_client(redis=BrokenRedis()) as client:
        body = client.get("/readyz").json()
        assert body["ready"] is True
        assert body["cache"] is False


def test_version(client):
    assert client.get("/version").json() == {"version": "test", "cache_enabled": True}
