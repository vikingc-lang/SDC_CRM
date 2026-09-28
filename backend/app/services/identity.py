"""Identity: TOTP two-factor authentication, recovery codes, sign-in policy and OpenID Connect SSO.

TOTP follows RFC 6238 (SHA-1, 6 digits, 30 s steps), which every authenticator app supports.
Secrets are stored Fernet-encrypted; recovery codes are stored as sha256 hashes and are single use.

SSO uses the OpenID Connect authorization-code flow with PKCE against any compliant IdP
(Microsoft Entra ID, Okta, Google Workspace, Keycloak, ADFS, Ping). The state, nonce and PKCE
verifier stay server-side in ``sso_login_states``; the ID token is verified against the IdP's JWKS.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.config import settings
from app.core.rbac import ROLES
from app.models import SsoLoginState, User
from app.services import app_settings
from app.services.mail import decrypt_secret, encrypt_secret

ISSUER_LABEL = "Cirra"
STEP_SECONDS = 30
RECOVERY_CODES = 10
SSO_STATE_TTL = timedelta(minutes=10)


class IdentityError(Exception):
    pass


# ---- TOTP ------------------------------------------------------------------------------------

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // STEP_SECONDS))


def match_totp(secret_b32: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """The accepted time step (±1 step of clock drift), or None. A step at or before ``last_step`` is a replay."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    now = int((at or time.time()) // STEP_SECONDS)
    for step in (now, now - 1, now + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(_hotp(secret_b32, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, email: str) -> str:
    label = urllib.parse.quote(f"{ISSUER_LABEL}:{email}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={ISSUER_LABEL}&algorithm=SHA1&digits=6&period={STEP_SECONDS}"


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg

    buf = io.BytesIO()
    qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
    return buf.getvalue().decode()


# ---- recovery codes ---------------------------------------------------------------------------

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes() -> tuple[list[str], list[str]]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    return codes, [_hash_code(c) for c in codes]


# ---- enrolment and verification ---------------------------------------------------------------

def begin_enrollment(user: User) -> dict:
    """Stage a new secret (not active until confirmed with a valid code)."""
    if user.mfa_enabled:
        raise IdentityError("Two-factor authentication is already on. Turn it off first to enrol a new device.")
    secret = new_totp_secret()
    user.mfa_secret = encrypt_secret(secret)
    user.mfa_last_step = None
    uri = otpauth_uri(secret, user.email)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg(uri)}


def confirm_enrollment(user: User, code: str) -> list[str]:
    if user.mfa_enabled:
        raise IdentityError("Two-factor authentication is already on")
    if not user.mfa_secret:
        raise IdentityError("Start enrolment first")
    step = match_totp(decrypt_secret(user.mfa_secret), code, None)
    if step is None:
        raise IdentityError("That code didn't match. Check the time on your phone and enter the current 6-digit code.")
    codes, hashes = new_recovery_codes()
    user.mfa_enabled, user.mfa_last_step, user.mfa_recovery_hashes = True, step, hashes
    user.mfa_enrolled_at = datetime.now(timezone.utc)
    return codes


def verify_second_factor(user: User, code: str) -> str:
    """Accept a TOTP code or an unused recovery code. Returns 'totp' or 'recovery'."""
    if not user.mfa_enabled or not user.mfa_secret:
        raise IdentityError("Two-factor authentication is not set up")
    step = match_totp(decrypt_secret(user.mfa_secret), code, user.mfa_last_step)
    if step is not None:
        user.mfa_last_step = step
        return "totp"
    h = _hash_code(code)
    if len(code.replace("-", "").strip()) == 12 and h in (user.mfa_recovery_hashes or []):
        user.mfa_recovery_hashes = [x for x in user.mfa_recovery_hashes if x != h]
        return "recovery"
    raise IdentityError("Invalid or already-used code")


def disable_mfa(user: User) -> None:
    user.mfa_enabled, user.mfa_secret, user.mfa_recovery_hashes = False, None, []
    user.mfa_last_step = user.mfa_enrolled_at = None
    user.session_version = (user.session_version or 0) + 1


# ---- sign-in policy ---------------------------------------------------------------------------

async def policy(db: AsyncSession) -> dict:
    return await app_settings.get(db, "security")


def mfa_required_for(pol: dict, user: User) -> bool:
    return user.role in (pol.get("mfa_required_roles") or [])


def password_login_allowed(pol: dict, user: User) -> bool:
    """With SSO enforced, only Super Admins keep password sign-in (break-glass if the IdP is down)."""
    sso = pol.get("sso") or {}
    return not (sso.get("enabled") and sso.get("enforce")) or user.role == "super_admin"


def public_methods(pol: dict) -> dict:
    sso = pol.get("sso") or {}
    on = bool(sso.get("enabled") and sso.get("issuer") and sso.get("client_id"))
    return {"password": not (on and sso.get("enforce")), "sso": {"enabled": on, "display_name": sso.get("display_name") or "Single sign-on"}}


def admin_view(pol: dict) -> dict:
    """Policy for the admin screen: the client secret is write-only."""
    sso = dict(pol.get("sso") or {})
    sso["client_secret_set"] = bool(sso.pop("client_secret_enc", None))
    sso["redirect_uri"] = redirect_uri()
    return {**pol, "sso": sso}


async def save_policy(db: AsyncSession, body: dict) -> dict:
    current = await policy(db)
    sso_in = dict(body.get("sso") or {})
    secret = sso_in.pop("client_secret", None)
    sso_in.pop("client_secret_set", None)
    sso_in.pop("redirect_uri", None)
    sso = {**(current.get("sso") or {}), **sso_in}
    if secret:
        sso["client_secret_enc"] = encrypt_secret(secret)
    if sso.get("issuer"):
        sso["issuer"] = sso["issuer"].strip().rstrip("/")
    sso["allowed_domains"] = sorted({d.strip().lower().lstrip("@") for d in sso.get("allowed_domains") or [] if d.strip()})
    if sso.get("enabled") and not (sso.get("issuer") and sso.get("client_id")):
        raise IdentityError("Issuer URL and client ID are required to turn on single sign-on")
    roles = set(body.get("mfa_required_roles", current.get("mfa_required_roles") or []))
    if roles - set(ROLES):
        raise IdentityError(f"Unknown role: {', '.join(sorted(roles - set(ROLES)))}")
    if sso.get("default_role") not in ROLES or sso.get("default_role") in ("super_admin", "partner"):
        raise IdentityError("Auto-provisioned users can't default to Super Admin or Partner")
    value = {"mfa_required_roles": sorted(roles), "sso": sso}
    return admin_view(await app_settings.put(db, "security", value))


# ---- OpenID Connect ---------------------------------------------------------------------------

def redirect_uri() -> str:
    return f"{tenancy.web_url()}/login/sso/callback"


_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwk_clients: dict[str, jwt.PyJWKClient] = {}


async def discover(issuer: str) -> dict:
    hit = _discovery_cache.get(issuer)
    if hit and hit[0] > time.time():
        return hit[1]
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise IdentityError(f"Couldn't read the identity provider's configuration at {url}: {e}") from e
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(k):
            raise IdentityError(f"The identity provider's configuration has no {k}")
    _discovery_cache[issuer] = (time.time() + 3600, doc)
    return doc


def safe_return_path(value: str | None) -> bool:
    """Only same-site paths: '/x' but not '//host', a slash followed by a backslash, schemes or control characters
    (browsers normalise backslashes to slashes, so '/' + backslash + 'evil.com' would otherwise leave the site)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    parts = urllib.parse.urlsplit(value)
    return not parts.scheme and not parts.netloc and all(ord(ch) >= 32 for ch in value)


async def start_sso(db: AsyncSession, return_to: str | None) -> str:
    sso = (await policy(db)).get("sso") or {}
    if not (sso.get("enabled") and sso.get("issuer") and sso.get("client_id")):
        raise IdentityError("Single sign-on is not configured")
    doc = await discover(sso["issuer"])
    await db.execute(delete(SsoLoginState).where(SsoLoginState.created_at < func.now() - SSO_STATE_TTL))
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    if not safe_return_path(return_to):
        return_to = None
    db.add(SsoLoginState(state=state, nonce=nonce, code_verifier=verifier, return_to=return_to))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {"response_type": "code", "client_id": sso["client_id"], "redirect_uri": redirect_uri(),
              "scope": sso.get("scopes") or "openid email profile", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{doc['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


async def _exchange_code(doc: dict, sso: dict, code: str, verifier: str) -> dict:
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "code_verifier": verifier,
            "client_id": sso["client_id"]}
    if sso.get("client_secret_enc"):
        data["client_secret"] = decrypt_secret(sso["client_secret_enc"])
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(doc["token_endpoint"], data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise IdentityError(f"Couldn't reach the identity provider: {e}") from e
    if r.status_code != 200:
        raise IdentityError(f"The identity provider rejected the sign-in ({r.status_code}: {r.text[:200]})")
    return r.json()


async def _signing_key(jwks_uri: str, token: str):
    client = _jwk_clients.setdefault(jwks_uri, jwt.PyJWKClient(jwks_uri, cache_keys=True))
    return (await asyncio.to_thread(client.get_signing_key_from_jwt, token)).key


async def verify_id_token(doc: dict, sso: dict, id_token: str, nonce: str) -> dict:
    try:
        key = await _signing_key(doc["jwks_uri"], id_token)
        claims = jwt.decode(id_token, key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                            audience=sso["client_id"], issuer=doc.get("issuer") or sso["issuer"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise IdentityError(f"The identity provider's ID token failed verification: {e}") from e
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise IdentityError("The sign-in response doesn't match this sign-in attempt (nonce mismatch)")
    return claims


async def complete_sso(db: AsyncSession, code: str, state: str) -> tuple[User, str | None]:
    row = await db.get(SsoLoginState, state)
    if row is None or row.created_at < datetime.now(timezone.utc) - SSO_STATE_TTL:
        raise IdentityError("This sign-in link has expired. Start again from the sign-in page.")
    await db.delete(row)
    sso = (await policy(db)).get("sso") or {}
    if not sso.get("enabled"):
        raise IdentityError("Single sign-on is not configured")
    doc = await discover(sso["issuer"])
    tokens = await _exchange_code(doc, sso, code, row.code_verifier)
    if not tokens.get("id_token"):
        raise IdentityError("The identity provider returned no ID token (is the 'openid' scope allowed?)")
    claims = await verify_id_token(doc, sso, tokens["id_token"], row.nonce)
    user = await _link_user(db, sso, claims)
    return user, row.return_to


async def _link_user(db: AsyncSession, sso: dict, claims: dict) -> User:
    subject = f"{claims['iss']}|{claims['sub']}"
    verified_email = (claims.get("email") or "").strip().lower() if claims.get("email_verified") is True else ""
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    user = (await db.execute(select(User).where(User.sso_subject == subject))).scalar_one_or_none()
    if user is None:
        if not email or "@" not in email:
            raise IdentityError("The identity provider didn't share an email address. Add the 'email' scope or claim.")
        if claims.get("email_verified") is False:
            raise IdentityError(f"{email} is not verified at the identity provider")
        domains = [d.lower() for d in sso.get("allowed_domains") or []]
        domain_trusted = email.rsplit("@", 1)[1] in domains
        if domains and not domain_trusted:
            raise IdentityError(f"{email} is not in an allowed sign-in domain")
        # Linking an IdP identity to an existing Cirra user (possibly an admin) needs proof the IdP owns the address:
        # a verified 'email' claim, or a domain the admin listed as allowed. A bare username/UPN is not enough.
        if not (email == verified_email or domain_trusted):
            existing = (await db.execute(select(User.id).where(func.lower(User.email) == email))).first()
            if existing is not None or not sso.get("auto_provision"):
                raise IdentityError(f"Can't confirm {email} belongs to you: the identity provider didn't mark it verified. "
                                    "An administrator can list your email domain under allowed sign-in domains.")
        user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if user is None:
            if not sso.get("auto_provision"):
                raise IdentityError(f"No Cirra user exists for {email}. Ask an administrator to invite you.")
            if not await tenancy.can_add_user(db):
                raise IdentityError("This workspace has reached its user limit. Ask an administrator to make room.")
            user = User(email=email, full_name=(claims.get("name") or email.split("@")[0])[:150], role=sso.get("default_role") or "sdr",
                        password_hash="!sso", is_active=True)
            db.add(user)
        elif user.role == "partner":
            raise IdentityError("Partner accounts sign in through the partner portal")
        user.sso_subject = subject
    if not user.is_active:
        raise IdentityError("This user is deactivated")
    return user
