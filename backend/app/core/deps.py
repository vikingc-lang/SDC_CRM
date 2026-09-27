"""FastAPI dependencies: authentication and role-based access."""
import uuid

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

bearer = HTTPBearer(auto_error=False)


API_KEY_PREFIX = "ck_"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


async def get_current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_db)
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    raw = creds.credentials if creds else request.headers.get("x-api-key")
    if not raw:
        raise unauthorized
    if raw.startswith(API_KEY_PREFIX):  # integration API key: acts as its user (services/developer.py)
        from app.services import developer

        found = await developer.authenticate(db, raw)
        if found is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, expired or revoked API key")
        user, key = found
        if key.read_only and request.method not in SAFE_METHODS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This API key is read-only")
        request.state.api_key_id = key.id
        current_user_id.set(user.id)
        return user
    try:
        payload = decode_access_token(raw)
        user = await db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or not user.is_active or payload.get("sv", 0) != (user.session_version or 0):
        raise unauthorized
    current_user_id.set(user.id)  # attributes audit-trail entries to this user
    return user


async def human_user(request: Request, user: User = Depends(get_current_user)) -> User:
    """For sign-in security and key management: a person's session, never an API key."""
    if getattr(request.state, "api_key_id", None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API keys can't be used for this")
    return user


async def require_writer(user: User = Depends(get_current_user)) -> User:
    if user.role == "read_only":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only users cannot modify records")
    return user
