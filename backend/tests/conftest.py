import pytest

from app.db.session import engine


@pytest.fixture(autouse=True)
async def dispose_async_engine_after_test():
    yield
    await engine.dispose()
