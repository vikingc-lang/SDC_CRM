"""Password hashing and JWT issuance."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(subject: str, role: str, session_version: int = 0, amr: list[str] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": subject, "role": role, "sv": session_version, "amr": amr or ["pwd"], "exp": expire},
                      settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if "purpose" in payload:  # a pending-MFA token is not a session
        raise jwt.InvalidTokenError("not an access token")
    return payload


def create_pending_token(subject: str, purpose: str, minutes: int = 10) -> str:
    """Short-lived token proving the password step passed; only redeemable for the named second step."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({"sub": subject, "purpose": purpose, "exp": expire}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_pending_token(token: str, purpose: str) -> str:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong token purpose")
    return payload["sub"]
