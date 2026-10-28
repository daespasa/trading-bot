import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("MODELS_DIR", tempfile.mkdtemp())
os.environ["COLLECTOR_ENABLED"] = "0"

import pytest_asyncio  # noqa: E402

from app.database import session  # noqa: E402


@pytest_asyncio.fixture
async def db(tmp_path):
    session.init_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await session.create_all()
    yield session
    await session.engine().dispose()
