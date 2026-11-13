import redis


def create(client, name="Butter Chicken", price_cents=1599):
    resp = client.post("/items", json={"name": name, "price_cents": price_cents})
    assert resp.status_code == 201
    return resp.json()


def test_create_and_read_item(client):
    item = create(client)
    resp = client.get(f"/items/{item['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Butter Chicken"
    assert resp.json()["price_cents"] == 1599


def test_second_read_is_served_from_cache(client):
    item = create(client)
    first = client.get(f"/items/{item['id']}")
    second = client.get(f"/items/{item['id']}")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_update_invalidates_cache(client):
    item = create(client)
    client.get(f"/items/{item['id']}")  # warm the cache
    resp = client.put(f"/items/{item['id']}", json={"name": "Paneer Tikka", "price_cents": 1399})
    assert resp.status_code == 200

    after = client.get(f"/items/{item['id']}")
    assert after.headers["X-Cache"] == "MISS"
    assert after.json()["name"] == "Paneer Tikka"
    assert after.json()["price_cents"] == 1399


def test_delete_invalidates_cache(client):
    item = create(client)
    client.get(f"/items/{item['id']}")
    assert client.delete(f"/items/{item['id']}").status_code == 204
    assert client.get(f"/items/{item['id']}").status_code == 404


def test_missing_item_returns_404(client):
    assert client.get("/items/999").status_code == 404
    assert client.put("/items/999", json={"name": "x", "price_cents": 1}).status_code == 404
    assert client.delete("/items/999").status_code == 404


def test_rejects_invalid_input(client):
    assert client.post("/items", json={"name": "", "price_cents": 100}).status_code == 422
    assert client.post("/items", json={"name": "Naan", "price_cents": -1}).status_code == 422


def test_cache_disabled_always_reads_database(make_client):
    with make_client(cache_enabled=False) as client:
        item = create(client)
        assert client.get(f"/items/{item['id']}").headers["X-Cache"] == "BYPASS"
        assert client.get(f"/items/{item['id']}").headers["X-Cache"] == "BYPASS"


class BrokenRedis:
    """Stands in for a Redis server that is down."""

    def _fail(self, *args, **kwargs):
        raise redis.ConnectionError("redis is down")

    get = set = delete = ping = _fail


def test_service_keeps_working_when_redis_is_down(make_client):
    with make_client(redis=BrokenRedis()) as client:
        item = create(client)
        resp = client.get(f"/items/{item['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Butter Chicken"
        assert client.put(
            f"/items/{item['id']}", json={"name": "Dal", "price_cents": 999}
        ).status_code == 200
