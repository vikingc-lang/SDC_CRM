"""Integration tests run against TEST_DATABASE_URL (a disposable Postgres + pgvector database)."""
import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://cirra_user:cirra_secure_password@localhost:5432/cirra_test"
)
os.environ["LLM_PROVIDER"] = "heuristic"
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["DB_NULL_POOL"] = "true"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")
    yield


@pytest.fixture(scope="session")
async def seeded(database):
    from app.seed import seed

    await seed(reset=True)


@pytest.fixture(scope="session")
async def client(seeded):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/v1/auth/login", json={"username": "marcus@cirra.demo", "password": "cirra123"})
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        yield c
