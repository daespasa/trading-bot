"""Async SQLite engine in WAL mode. Writes go through `write()` which serialises them with one lock:
SQLite allows a single writer anyway, and serialising in-process avoids 'database is locked' retries.
"""
import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

_engine = None
_sessionmaker = None
_write_lock = asyncio.Lock()


def _pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA cache_size=-16000")  # 16 MB page cache
    cur.close()


def init_engine(url: str | None = None):
    global _engine, _sessionmaker, _write_lock
    url = url or f"sqlite+aiosqlite:///{settings.db_path}"
    _engine = create_async_engine(url)
    event.listen(_engine.sync_engine, "connect", _pragmas)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    _write_lock = asyncio.Lock()
    return _engine


def engine():
    return _engine


@asynccontextmanager
async def read():
    async with _sessionmaker() as s:
        yield s


@asynccontextmanager
async def write():
    """Serialised write transaction: commits on success, rolls back on error."""
    async with _write_lock:
        async with _sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise


async def create_all():
    """Used by tests. Production uses `alembic upgrade head`."""
    from app.database.models import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
