import logging
import time
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # In-memory SQLite needs a single shared connection across threads.
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_schema(engine: Engine, attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Create tables, retrying while the database is still starting up."""
    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(engine)
            return
        except Exception as exc:  # noqa: BLE001 - any connection error is retryable here
            if attempt == attempts:
                raise
            log.warning("database not ready (attempt %d/%d): %s", attempt, attempts, exc)
            time.sleep(delay_seconds)


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
