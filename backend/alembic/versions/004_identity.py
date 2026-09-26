"""Identity: two-factor authentication (TOTP + recovery codes) and OpenID Connect single sign-on.

Adds MFA state to users (encrypted TOTP secret, hashed single-use recovery codes, the last
accepted time step for replay protection), the SSO subject a user is linked to, a session
version that revokes outstanding tokens when MFA is reset, and short-lived SSO login state
(state, nonce, PKCE verifier) for the authorization-code flow.

Revision ID: 004_identity
Revises: 003_lead_to_order
Create Date: 2026-09-26
"""
from alembic import op

revision = "004_identity"
down_revision = "003_lead_to_order"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE users
    ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN mfa_secret TEXT,
    ADD COLUMN mfa_recovery_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN mfa_last_step BIGINT,
    ADD COLUMN mfa_enrolled_at TIMESTAMPTZ,
    ADD COLUMN sso_subject VARCHAR(255),
    ADD COLUMN session_version INT NOT NULL DEFAULT 0,
    ADD COLUMN last_login_at TIMESTAMPTZ;
CREATE UNIQUE INDEX ux_users_sso_subject ON users (sso_subject) WHERE sso_subject IS NOT NULL;

CREATE TABLE sso_login_states (
    state VARCHAR(64) PRIMARY KEY,
    nonce VARCHAR(64) NOT NULL,
    code_verifier VARCHAR(128) NOT NULL,
    return_to VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS sso_login_states;
DROP INDEX IF EXISTS ux_users_sso_subject;
ALTER TABLE users
    DROP COLUMN IF EXISTS mfa_enabled,
    DROP COLUMN IF EXISTS mfa_secret,
    DROP COLUMN IF EXISTS mfa_recovery_hashes,
    DROP COLUMN IF EXISTS mfa_last_step,
    DROP COLUMN IF EXISTS mfa_enrolled_at,
    DROP COLUMN IF EXISTS sso_subject,
    DROP COLUMN IF EXISTS session_version,
    DROP COLUMN IF EXISTS last_login_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
