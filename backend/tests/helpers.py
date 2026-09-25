from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def login_as(email: str, password: str = "relate123"):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/v1/auth/login", json={"username": email, "password": password})
        assert resp.status_code == 200, resp.text
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        yield c
