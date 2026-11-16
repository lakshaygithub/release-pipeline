import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.cache import ItemCache
from app.config import Settings, load_settings
from app.db import Item, init_schema, make_engine, make_session_factory, ping

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0)


class ItemOut(ItemIn):
    id: int
    updated_at: str


class ItemPage(BaseModel):
    items: list[ItemOut]
    next_after_id: int | None


def _set_lookup_timing(response: Response, start: float) -> None:
    # Standard Server-Timing header: how long the cache or database lookup took
    # inside the server, separate from network and client overhead.
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["Server-Timing"] = f"lookup;dur={elapsed_ms:.3f}"


def _to_out(item: Item) -> dict:
    # MySQL DATETIME drops the timezone and microseconds, so normalise here to
    # keep responses identical whether they come from a write, the DB or the cache.
    ts = item.updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return {
        "id": item.id,
        "name": item.name,
        "price_cents": item.price_cents,
        "updated_at": ts.replace(microsecond=0).isoformat(),
    }


def create_app(
    settings: "Settings | None" = None,
    engine: "Engine | None" = None,
    redis_client: "redis.Redis | None" = None,
) -> FastAPI:
    settings = settings or load_settings()
    engine = engine or make_engine(settings.database_url)
    if redis_client is None and settings.cache_enabled:
        redis_client = redis.Redis.from_url(
            settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_schema(engine)
        yield
        engine.dispose()

    app = FastAPI(title="release-pipeline", version=settings.app_version, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.cache = ItemCache(redis_client, settings.cache_ttl_seconds, settings.cache_enabled)

    def get_session(request: Request):
        session = request.app.state.session_factory()
        try:
            yield session
        finally:
            session.close()

    def get_cache(request: Request) -> ItemCache:
        return request.app.state.cache

    @app.get("/healthz")
    def healthz():
        """Liveness: the process is up. Kubernetes restarts the pod if this fails."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(response: Response, cache: ItemCache = Depends(get_cache)):
        """Readiness: safe to send traffic. A failing check keeps a new pod out of
        the load balancer, which is what lets a bad rollout be caught and undone."""
        db_ok = ping(engine)
        cache_ok = cache.ping()
        # Redis is optional (the service degrades to the database), so only the
        # database and the release itself decide readiness.
        ready = db_ok and not settings.fail_readiness
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "ready": ready,
            "database": db_ok,
            "cache": cache_ok,
            "version": settings.app_version,
        }

    @app.get("/version")
    def version(cache: ItemCache = Depends(get_cache)):
        return {"version": settings.app_version, "cache_enabled": cache.enabled}

    @app.post("/items", status_code=status.HTTP_201_CREATED, response_model=ItemOut)
    def create_item(body: ItemIn, session: Session = Depends(get_session)):
        item = Item(name=body.name, price_cents=body.price_cents)
        session.add(item)
        session.commit()
        return _to_out(item)

    @app.get("/items", response_model=ItemPage)
    def list_items(
        limit: int = Query(20, ge=1, le=100),
        after_id: int = Query(0, ge=0),
        session: Session = Depends(get_session),
    ):
        """Keyset pagination: pass the last id you saw as after_id. Unlike OFFSET,
        this stays fast on deep pages and doesn't skip or repeat rows when items
        are added or deleted between requests."""
        rows = session.scalars(
            select(Item).where(Item.id > after_id).order_by(Item.id).limit(limit + 1)
        ).all()
        page = rows[:limit]
        next_after_id = page[-1].id if len(rows) > limit else None
        return {"items": [_to_out(i) for i in page], "next_after_id": next_after_id}

    @app.get("/items/{item_id}", response_model=ItemOut)
    def read_item(
        item_id: int,
        response: Response,
        session: Session = Depends(get_session),
        cache: ItemCache = Depends(get_cache),
    ):
        start = time.perf_counter()
        cached = cache.get(item_id)
        if cached is not None:
            _set_lookup_timing(response, start)
            response.headers["X-Cache"] = "HIT"
            return cached
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        out = _to_out(item)
        _set_lookup_timing(response, start)
        cache.set(item_id, out)
        response.headers["X-Cache"] = "MISS" if cache.enabled else "BYPASS"
        return out

    @app.put("/items/{item_id}", response_model=ItemOut)
    def update_item(
        item_id: int,
        body: ItemIn,
        session: Session = Depends(get_session),
        cache: ItemCache = Depends(get_cache),
    ):
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        item.name = body.name
        item.price_cents = body.price_cents
        session.commit()
        # Write to the database first, then drop the cached copy, so the next
        # read repopulates the cache from the source of truth.
        cache.invalidate(item_id)
        return _to_out(item)

    @app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(
        item_id: int,
        session: Session = Depends(get_session),
        cache: ItemCache = Depends(get_cache),
    ):
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        session.delete(item)
        session.commit()
        cache.invalidate(item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()
