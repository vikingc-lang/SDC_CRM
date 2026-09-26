"""Sign-in security: TOTP two-factor auth, recovery codes, MFA policy, session revocation and OIDC single sign-on."""
import time
import urllib.parse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.services import identity
from tests.helpers import login_as

API = "/api/v1"


async def _new_user(admin, email, role="account_executive"):
    r = await admin.post(f"{API}/admin/users", json={"email": email, "full_name": "Test User", "role": role, "password": "s3cure-pass!"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _anon():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _password(c, email, password="s3cure-pass!"):
    return await c.post(f"{API}/auth/login", json={"username": email, "password": password})


def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1 seed "12345678901234567890" at T=59 -> 94287082 (last 6 digits)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert identity.totp_now(secret, at=59) == "287082"
    step = identity.match_totp(secret, "287082", None, at=59)
    assert step == 1
    assert identity.match_totp(secret, "287082", step, at=59) is None  # replay of the same step is refused


async def test_enrol_sign_in_and_recovery_codes(client):
    async with login_as("admin@cirra.demo") as admin:
        await _new_user(admin, "mfa.user@cirra.demo")
    async with _anon() as c:
        tok = (await _password(c, "mfa.user@cirra.demo")).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        start = (await c.post(f"{API}/auth/mfa/enroll/start", json={})).json()
        assert start["otpauth_uri"].startswith("otpauth://totp/Cirra:") and "<svg" in start["qr_svg"]
        assert (await c.post(f"{API}/auth/mfa/enroll/confirm", json={"code": "000000"})).status_code == 400
        conf = await c.post(f"{API}/auth/mfa/enroll/confirm", json={"code": identity.totp_now(start["secret"])})
        codes = conf.json()["recovery_codes"]
        assert len(codes) == 10
        assert (await c.get(f"{API}/users/me")).json()["security"]["mfa_enabled"] is True

    async with _anon() as c:
        step1 = (await _password(c, "mfa.user@cirra.demo")).json()
        assert step1.get("mfa_required") is True and "access_token" not in step1
        # the pending token is not a session
        assert (await c.get(f"{API}/users/me", headers={"Authorization": f"Bearer {step1['mfa_token']}"})).status_code == 401
        bad = await c.post(f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": "123456"})
        assert bad.status_code == 401
        ok = await c.post(f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": codes[0]})
        assert ok.status_code == 200 and ok.json()["access_token"]
        # a recovery code works exactly once
        again = await c.post(f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": codes[0]})
        assert again.status_code == 401
        c.headers["Authorization"] = f"Bearer {ok.json()['access_token']}"
        assert (await c.get(f"{API}/users/me")).json()["security"]["recovery_codes_left"] == 9


async def test_policy_forces_enrolment_and_admin_reset_revokes_sessions(client):
    async with login_as("admin@cirra.demo") as admin:
        uid = await _new_user(admin, "forced.sdr@cirra.demo", role="sdr")
        r = await admin.put(f"{API}/admin/security", json={"mfa_required_roles": ["sdr"]})
        assert r.status_code == 200 and r.json()["mfa_required_roles"] == ["sdr"]
        try:
            async with _anon() as c:
                step1 = (await _password(c, "forced.sdr@cirra.demo")).json()
                assert step1.get("mfa_setup_required") is True
                start = (await c.post(f"{API}/auth/mfa/enroll/start", json={"mfa_token": step1["mfa_token"]})).json()
                done = (await c.post(f"{API}/auth/mfa/enroll/confirm",
                                     json={"mfa_token": step1["mfa_token"], "code": identity.totp_now(start["secret"])})).json()
                assert done["access_token"] and len(done["recovery_codes"]) == 10
                c.headers["Authorization"] = f"Bearer {done['access_token']}"
                assert (await c.get(f"{API}/users/me")).status_code == 200
                # role requires MFA: the user can't switch it off
                off = await c.post(f"{API}/auth/mfa/disable", json={"password": "s3cure-pass!", "code": done["recovery_codes"][0]})
                assert off.status_code == 422
                # lost phone: an admin reset signs the user out everywhere
                assert (await admin.post(f"{API}/admin/users/{uid}/reset-mfa")).status_code == 200
                assert (await c.get(f"{API}/users/me")).status_code == 401
                assert (await _password(c, "forced.sdr@cirra.demo")).json().get("mfa_setup_required") is True
        finally:
            await admin.put(f"{API}/admin/security", json={"mfa_required_roles": []})


class FakeIdP:
    issuer = "https://idp.example.test"

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict = {}

    def doc(self):
        return {"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token", "jwks_uri": f"{self.issuer}/jwks"}

    def id_token(self, nonce, **over):
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": "cirra-client", "sub": "idp-user-1", "iat": now, "exp": now + 300, "nonce": nonce,
                  "email": "priya@cirra.demo", "email_verified": True, **self.claims, **over}
        return jwt.encode(claims, self.key, algorithm="RS256")


async def test_oidc_sso_sign_in(client, monkeypatch):
    idp = FakeIdP()
    issued: dict = {}

    async def discover(issuer):
        assert issuer == idp.issuer
        return idp.doc()

    async def exchange(doc, sso, code, verifier):
        assert code == "auth-code" and verifier
        return {"id_token": issued["token"]}

    async def signing_key(jwks_uri, token):
        return idp.key.public_key()

    monkeypatch.setattr(identity, "discover", discover)
    monkeypatch.setattr(identity, "_exchange_code", exchange)
    monkeypatch.setattr(identity, "_signing_key", signing_key)

    async with login_as("admin@cirra.demo") as admin:
        cfg = {"sso": {"enabled": True, "enforce": True, "display_name": "Acme Okta", "issuer": idp.issuer + "/",
                       "client_id": "cirra-client", "client_secret": "shh", "allowed_domains": ["cirra.demo"]}}
        saved = (await admin.put(f"{API}/admin/security", json=cfg)).json()
        assert saved["sso"]["client_secret_set"] is True and "client_secret_enc" not in saved["sso"]
        assert saved["sso"]["issuer"] == idp.issuer
        try:
            async with _anon() as c:
                assert (await c.get(f"{API}/auth/methods")).json() == {"password": False, "sso": {"enabled": True, "display_name": "Acme Okta"}}
                # SSO enforced: password sign-in is refused except for Super Admins (break-glass)
                assert (await c.post(f"{API}/auth/login", json={"username": "priya@cirra.demo", "password": "cirra123"})).status_code == 403
                assert (await c.post(f"{API}/auth/login", json={"username": "admin@cirra.demo", "password": "cirra123"})).status_code == 200

                async def begin():
                    url = (await c.get(f"{API}/auth/sso/start", params={"return_to": "/pipeline"})).json()["authorization_url"]
                    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
                    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].endswith("/login/sso/callback")
                    return q

                q = await begin()
                issued["token"] = idp.id_token("wrong-nonce")
                assert (await c.post(f"{API}/auth/sso/callback", json={"code": "auth-code", "state": q["state"]})).status_code == 401

                q = await begin()
                issued["token"] = idp.id_token(q["nonce"])
                ok = await c.post(f"{API}/auth/sso/callback", json={"code": "auth-code", "state": q["state"]})
                assert ok.status_code == 200, ok.text
                assert ok.json()["return_to"] == "/pipeline"
                me = (await c.get(f"{API}/users/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})).json()
                assert me["email"] == "priya@cirra.demo" and me["security"]["sso_linked"] is True
                # the state is single use
                assert (await c.post(f"{API}/auth/sso/callback", json={"code": "auth-code", "state": q["state"]})).status_code == 401

                # unknown users are refused unless auto-provisioning is on; other domains always are
                q = await begin()
                issued["token"] = idp.id_token(q["nonce"], sub="idp-user-2", email="new.hire@cirra.demo")
                r = await c.post(f"{API}/auth/sso/callback", json={"code": "auth-code", "state": q["state"]})
                assert r.status_code == 401 and "invite" in r.json()["detail"]
                q = await begin()
                issued["token"] = idp.id_token(q["nonce"], sub="idp-user-3", email="someone@elsewhere.com")
                assert (await c.post(f"{API}/auth/sso/callback", json={"code": "auth-code", "state": q["state"]})).status_code == 401
        finally:
            await admin.put(f"{API}/admin/security", json={"sso": {"enabled": False, "enforce": False}})
