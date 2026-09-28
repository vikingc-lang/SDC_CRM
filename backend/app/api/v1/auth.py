import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id, log_action
from app.core.database import get_db
from app.core.deps import bearer, get_current_user, human_user
from app.core.rbac import ACTIONS, RESOURCES, Principal, get_principal, load_matrix
from app.core.security import create_access_token, create_pending_token, decode_pending_token, verify_password
from app.models import AuditLog, Partner, User
from app.schemas.crm import LoginRequest, TokenResponse
from app.services import identity
from app.services.identity import IdentityError

router = APIRouter(tags=["auth"])

MFA_VERIFY, MFA_ENROLL = "mfa_verify", "mfa_enroll"
# Brute-force protection, counted from the append-only audit trail (works across API processes, no extra table)
LOCKOUT_WINDOW = timedelta(minutes=15)
MFA_MAX_FAILURES = 5
LOGIN_MAX_FAILURES = 10


async def _recent_failures(db: AsyncSession, user_id: uuid.UUID, action: str) -> int:
    since = datetime.now(timezone.utc) - LOCKOUT_WINDOW
    return (await db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.record_id == user_id, AuditLog.action == action, AuditLog.created_at >= since))).scalar_one()


def _locked(what: str) -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many failed {what}. Wait 15 minutes and try again.")


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PendingCodeIn(CodeIn):
    mfa_token: str


class PendingIn(BaseModel):
    mfa_token: str | None = None


class ConfirmIn(CodeIn):
    mfa_token: str | None = None


class DisableIn(CodeIn):
    password: str


class SsoCallbackIn(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=64)


def _bad(e: IdentityError, code: int = 400) -> HTTPException:
    return HTTPException(code, str(e))


async def _issue(db: AsyncSession, user: User, amr: list[str], action: str = "login") -> TokenResponse:
    user.last_login_at = datetime.now(timezone.utc)
    current_user_id.set(user.id)
    log_action(db, action, "users", user.id, "+".join(amr))
    await db.commit()
    return TokenResponse(access_token=create_access_token(str(user.id), user.role, user.session_version or 0, amr))


async def _pending_user(db: AsyncSession, token: str, purpose: str) -> User:
    try:
        user = await db.get(User, uuid.UUID(decode_pending_token(token, purpose)))
    except (jwt.PyJWTError, ValueError, KeyError):
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your sign-in has expired. Enter your password again.")
    return user


@router.get("/auth/methods")
async def methods(db: AsyncSession = Depends(get_db)):
    """Public: which sign-in options the login page shows."""
    return identity.public_methods(await identity.policy(db))


@router.post("/auth/login", response_model=TokenResponse, response_model_exclude_none=True)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(func.lower(User.email) == body.username.strip().lower()))).scalar_one_or_none()
    if user is not None and await _recent_failures(db, user.id, "login_failed") >= LOGIN_MAX_FAILURES:
        raise _locked("sign-in attempts")
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        if user is not None:
            current_user_id.set(user.id)
            log_action(db, "login_failed", "users", user.id)
            await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    pol = await identity.policy(db)
    if not identity.password_login_allowed(pol, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your organisation signs in with single sign-on. Use the SSO button.")
    if user.mfa_enabled:
        return TokenResponse(mfa_required=True, mfa_token=create_pending_token(str(user.id), MFA_VERIFY))
    if identity.mfa_required_for(pol, user):
        return TokenResponse(mfa_setup_required=True, mfa_token=create_pending_token(str(user.id), MFA_ENROLL, minutes=20))
    return await _issue(db, user, ["pwd"])


@router.post("/auth/mfa/verify", response_model=TokenResponse, response_model_exclude_none=True)
async def mfa_verify(body: PendingCodeIn, db: AsyncSession = Depends(get_db)):
    user = await _pending_user(db, body.mfa_token, MFA_VERIFY)
    if await _recent_failures(db, user.id, "mfa_failed") >= MFA_MAX_FAILURES:
        raise _locked("two-factor codes")
    try:
        method = identity.verify_second_factor(user, body.code)
    except IdentityError as e:
        current_user_id.set(user.id)
        log_action(db, "mfa_failed", "users", user.id)
        await db.commit()
        raise _bad(e, status.HTTP_401_UNAUTHORIZED)
    if method == "recovery":
        log_action(db, "mfa_recovery_used", "users", user.id, f"{len(user.mfa_recovery_hashes)} codes left")
    return await _issue(db, user, ["pwd", "otp" if method == "totp" else "rc"])


async def _enrolling_user(db: AsyncSession, token: str | None, bearer_user: User | None) -> User:
    if token:
        return await _pending_user(db, token, MFA_ENROLL)
    if bearer_user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return bearer_user


async def _optional_user(request: Request, db: AsyncSession = Depends(get_db), creds=Depends(bearer)):
    """The signed-in person, if any. A stale or revoked bearer (left in the browser after an MFA reset) counts as
    no session rather than an error, so forced enrolment with an mfa_token still works."""
    if creds is None:
        return None
    try:
        user = await get_current_user(request, creds, db)
    except HTTPException:
        return None
    return await human_user(request, user)


@router.post("/auth/mfa/enroll/start")
async def mfa_enroll_start(body: PendingIn, db: AsyncSession = Depends(get_db), me: User | None = Depends(_optional_user)):
    """Begin TOTP enrolment: from Settings (signed in) or during a forced first sign-in (mfa_token)."""
    user = await _enrolling_user(db, body.mfa_token, me)
    try:
        out = identity.begin_enrollment(user)
    except IdentityError as e:
        raise _bad(e, 409)
    await db.commit()
    return out


@router.post("/auth/mfa/enroll/confirm")
async def mfa_enroll_confirm(body: ConfirmIn, db: AsyncSession = Depends(get_db), me: User | None = Depends(_optional_user)):
    user = await _enrolling_user(db, body.mfa_token, me)
    try:
        codes = identity.confirm_enrollment(user, body.code)
    except IdentityError as e:
        raise _bad(e)
    current_user_id.set(user.id)
    log_action(db, "mfa_enabled", "users", user.id)
    if body.mfa_token:  # forced enrolment finishes the sign-in
        token = await _issue(db, user, ["pwd", "otp"])
        return {"recovery_codes": codes, "access_token": token.access_token}
    await db.commit()
    return {"recovery_codes": codes}


@router.post("/auth/mfa/recovery-codes")
async def mfa_new_recovery_codes(body: CodeIn, db: AsyncSession = Depends(get_db), user: User = Depends(human_user)):
    try:
        identity.verify_second_factor(user, body.code)
    except IdentityError as e:
        raise _bad(e)
    codes, user.mfa_recovery_hashes = identity.new_recovery_codes()
    log_action(db, "mfa_codes_reissued", "users", user.id)
    await db.commit()
    return {"recovery_codes": codes}


@router.post("/auth/mfa/disable")
async def mfa_disable(body: DisableIn, db: AsyncSession = Depends(get_db), user: User = Depends(human_user)):
    if identity.mfa_required_for(await identity.policy(db), user):
        raise HTTPException(422, "Your role requires two-factor authentication, so it can't be turned off")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect password")
    try:
        identity.verify_second_factor(user, body.code)
    except IdentityError as e:
        raise _bad(e)
    identity.disable_mfa(user)
    log_action(db, "mfa_disabled", "users", user.id)
    token = await _issue(db, user, ["pwd", "otp"], action="session_renewed")
    return {"status": "disabled", "access_token": token.access_token}


@router.get("/auth/sso/start")
async def sso_start(return_to: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        url = await identity.start_sso(db, return_to)
    except IdentityError as e:
        raise _bad(e)
    await db.commit()
    return {"authorization_url": url}


@router.post("/auth/sso/callback")
async def sso_callback(body: SsoCallbackIn, db: AsyncSession = Depends(get_db)):
    try:
        user, return_to = await identity.complete_sso(db, body.code, body.state)
    except IdentityError as e:
        await db.commit()  # the used state row is consumed even on failure
        raise _bad(e, status.HTTP_401_UNAUTHORIZED)
    await db.flush()
    token = await _issue(db, user, ["sso"], action="login_sso")
    return {"access_token": token.access_token, "return_to": return_to}


@router.get("/users/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    matrix = await load_matrix(db, user.role)
    partner = await db.get(Partner, user.partner_id) if user.partner_id else None
    pol = await identity.policy(db)
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "manager_id": user.manager_id,
        "partner": {"id": partner.id, "name": partner.name, "tier": partner.tier} if partner else None,
        "permissions": {r: {a: matrix[r].allows(a) for a in ACTIONS} | {"scope": matrix[r].scope} for r in RESOURCES if r in matrix},
        "security": {"mfa_enabled": user.mfa_enabled, "mfa_required": identity.mfa_required_for(pol, user),
                     "recovery_codes_left": len(user.mfa_recovery_hashes or []), "sso_linked": bool(user.sso_subject),
                     "has_password": user.password_hash != "!sso"},
        "preferences": {"locale": user.locale, "timezone": user.timezone},
        "workspace": _workspace(),
    }


def _workspace() -> dict | None:
    """The tenant workspace this session belongs to (None in a single-workspace install)."""
    from app.core import tenancy

    if tenancy.slug() == tenancy.DEFAULT:
        return None
    info = tenancy._registry.get(tenancy.slug())
    return {"slug": tenancy.slug(), "name": info.name if info else tenancy.slug()}


LOCALES = ("en-US", "en-GB", "en-IN", "es-ES", "es-MX", "fr-FR", "de-DE", "hi-IN")


class PreferencesIn(BaseModel):
    locale: str | None = None
    timezone: str | None = None


@router.patch("/users/me/preferences")
async def set_preferences(body: PreferencesIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Language and number/date formats (``locale``; empty = follow the browser) and time zone."""
    data = body.model_dump(exclude_unset=True)
    if "locale" in data:
        if data["locale"] and data["locale"] not in LOCALES:
            raise HTTPException(422, f"Choose one of: {', '.join(LOCALES)}")
        user.locale = data["locale"] or None
    if "timezone" in data:
        tz = (data["timezone"] or "").strip() or None
        if tz:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise HTTPException(422, "Unknown time zone") from e
        user.timezone = tz
    await db.commit()
    return {"locale": user.locale, "timezone": user.timezone}


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), _: Principal = Depends(get_principal)):
    """The internal directory (for owner / assignee pickers). Partner portal users can't list staff."""
    users = (await db.execute(select(User).where(User.is_active.is_(True), User.role != "partner").order_by(User.full_name))).scalars().all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role} for u in users]
