import pytest

from app.db.session import engine
from app.composition import wire_application


@pytest.fixture(autouse=True)
async def dispose_async_engine_after_test():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def wire_inert_application_effects():
    wire_application()
