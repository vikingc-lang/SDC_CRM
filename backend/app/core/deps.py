"""FastAPI dependencies: authentication and role-based access."""
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_db)
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
        user = await db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or not user.is_active:
        raise unauthorized
    current_user_id.set(user.id)  # attributes audit-trail entries to this user
    return user


async def require_writer(user: User = Depends(get_current_user)) -> User:
    if user.role == "read_only":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only users cannot modify records")
    return user
