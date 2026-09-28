"""Role-based access control with row-level ownership isolation (pillar 10).

Permissions are a matrix of role x resource x {create, read, update, delete,
export} plus a scope: ``all`` (every record) or ``own`` (only records the user
owns: accounts they own or have deals on, and everything hanging off those
accounts). The matrix lives in ``role_permissions`` so admins can tune it at
runtime; ``DEFAULT_MATRIX`` seeds it and is the fallback.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import Account, Deal, RolePermission, User

ACTIONS = ("create", "read", "update", "delete", "export")
RESOURCES = (
    "accounts", "contacts", "deals", "activities", "tasks", "products", "quotes", "approvals", "documents",
    "contracts", "success", "finance", "partners", "reports", "admin", "audit", "data", "leads", "orders",
    "cases", "knowledge", "campaigns", "custom_objects",
)
ROLES = ("super_admin", "sales_manager", "account_executive", "sdr", "auditor", "partner", "support_agent", "marketing")
ROLE_LABELS = {
    "super_admin": "Super Admin", "sales_manager": "Sales Manager", "account_executive": "Account Executive",
    "sdr": "SDR", "auditor": "Auditor", "partner": "Partner (portal)", "support_agent": "Support Agent", "marketing": "Marketing",
}


def _p(spec: str, scope: str = "all") -> dict:
    """'CRUDE' style shorthand -> permission row."""
    return {"can_create": "C" in spec, "can_read": "R" in spec, "can_update": "U" in spec,
            "can_delete": "D" in spec, "can_export": "E" in spec, "scope": scope}


_SALES = ("accounts", "contacts", "deals", "activities", "tasks", "quotes", "documents")
DEFAULT_MATRIX: dict[str, dict[str, dict]] = {
    "super_admin": {r: _p("CRUDE") for r in RESOURCES},
    "sales_manager": {
        **{r: _p("CRUDE") for r in _SALES},
        "products": _p("RE"), "approvals": _p("RU"), "contracts": _p("CRUE"), "success": _p("CRUE"),
        "finance": _p("RE"), "partners": _p("CRUE"), "reports": _p("RE"), "admin": _p("R"), "audit": _p("R"),
        "data": _p("CRE"), "leads": _p("CRUDE"), "orders": _p("CRUE"), "cases": _p("CRUDE"), "knowledge": _p("CRUDE"), "campaigns": _p("CRUDE"),
        "custom_objects": _p("CRUDE"),
    },
    "account_executive": {
        "accounts": _p("CRU", "own"), "contacts": _p("CRUD", "own"), "deals": _p("CRU", "own"),
        "activities": _p("CRUD", "own"), "tasks": _p("CRUD", "own"), "quotes": _p("CRU", "own"),
        "documents": _p("CRU", "own"), "contracts": _p("R", "own"), "success": _p("R", "own"),
        "finance": _p("R", "own"), "products": _p("R"), "approvals": _p("R", "own"), "partners": _p("R"),
        "reports": _p("R", "own"), "leads": _p("CRU", "own"), "orders": _p("CR", "own"),
        "cases": _p("CRU", "own"), "knowledge": _p("R"), "campaigns": _p("R"), "custom_objects": _p("CRUD", "own"),
    },
    "sdr": {
        "accounts": _p("CR", "own"), "contacts": _p("CRU", "own"), "deals": _p("CR", "own"),
        "activities": _p("CRU", "own"), "tasks": _p("CRU", "own"), "products": _p("R"), "reports": _p("R", "own"),
        "leads": _p("CRUE", "own"), "cases": _p("R", "own"), "knowledge": _p("R"), "campaigns": _p("R"), "custom_objects": _p("CR", "own"),
    },
    # Service desk: every case, read-only view of customers, their own follow-ups
    "support_agent": {
        "cases": _p("CRUE"), "knowledge": _p("CRU"), "accounts": _p("R"), "contacts": _p("CRU"), "activities": _p("CR"),
        "tasks": _p("CRU", "own"), "products": _p("R"), "success": _p("R"), "reports": _p("R", "own"), "custom_objects": _p("CRU"),
    },
    # Demand generation: campaigns and every lead; read-only view of customers and the pipeline they influence
    "marketing": {
        "campaigns": _p("CRUDE"), "leads": _p("CRUE"), "contacts": _p("RU"), "accounts": _p("R"), "deals": _p("R"),
        "activities": _p("R"), "tasks": _p("CRU", "own"), "products": _p("R"), "reports": _p("RE", "own"), "knowledge": _p("R"),
        "custom_objects": _p("R"),
    },
    "auditor": {**{r: _p("RE") for r in RESOURCES if r not in ("admin",)}, "admin": _p("R")},
    "partner": {},
}


@dataclass
class Perm:
    can_create: bool = False
    can_read: bool = False
    can_update: bool = False
    can_delete: bool = False
    can_export: bool = False
    scope: str = "own"

    def allows(self, action: str) -> bool:
        return bool(getattr(self, f"can_{action}"))


@dataclass
class Principal:
    user: User
    matrix: dict[str, Perm] = field(default_factory=dict)
    shares: list[list[dict]] = field(default_factory=list)  # criteria of the account sharing rules for this role

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def perm(self, resource: str) -> Perm:
        return self.matrix.get(resource, Perm())

    def can(self, resource: str, action: str) -> bool:
        return self.perm(resource).allows(action)

    def is_own_scope(self, resource: str) -> bool:
        return self.perm(resource).scope == "own"

    # ---- row-level ownership --------------------------------------------------
    def team_deal_ids(self):
        """Deals this user works on as a deal-team member (or has a split on)."""
        from app.models import DealTeamMember

        return select(DealTeamMember.deal_id).where(DealTeamMember.user_id == self.user.id)

    def owned_account_ids(self, include_team: bool = True):
        """Subquery of account ids this user owns, sells into (owns or is on the team of a deal there), or sees
        through an account sharing rule."""
        visible = [Account.owner_id == self.user.id, Account.id.in_(select(Deal.account_id).where(Deal.owner_id == self.user.id))]
        if include_team:
            visible.append(Account.id.in_(select(Deal.account_id).where(Deal.id.in_(self.team_deal_ids()))))
        if self.shares:
            from app.services import reporting

            visible += [Account.id.in_(q) for q in (reporting.criteria_ids("accounts", c) for c in self.shares) if q is not None]
        return select(Account.id).where(or_(*visible))

    def scope_accounts(self, stmt, resource: str = "accounts", column=None):
        """Restrict ``stmt`` to visible accounts when the role's scope is ``own``."""
        if not self.is_own_scope(resource):
            return stmt
        return stmt.where((column if column is not None else Account.id).in_(self.owned_account_ids()))

    def scope_deals(self, stmt):
        if not self.is_own_scope("deals"):
            return stmt
        return stmt.where(or_(Deal.owner_id == self.user.id, Deal.account_id.in_(self.owned_account_ids()), Deal.id.in_(self.team_deal_ids())))

    async def ensure_account(self, db: AsyncSession, account_id: uuid.UUID, resource: str = "accounts") -> None:
        """404 (not 403) when a record is outside the user's scope, so existence isn't leaked."""
        if not self.is_own_scope(resource):
            return
        visible = (await db.execute(self.owned_account_ids().where(Account.id == account_id))).first()
        if visible is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


_cache: dict[tuple[str, str], tuple[float, dict[str, Perm]]] = {}  # (tenant, role)
_TTL = 30.0


def invalidate_cache() -> None:
    _cache.clear()


async def load_matrix(db: AsyncSession, role: str) -> dict[str, Perm]:
    key = (tenancy.slug(), role)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    rows = (await db.execute(select(RolePermission).where(RolePermission.role == role))).scalars().all()
    # defaults first, stored overrides on top: resources added in later releases get sensible access
    matrix = {res: Perm(**spec) for res, spec in DEFAULT_MATRIX.get(role, {}).items()}
    matrix.update({r.resource: Perm(r.can_create, r.can_read, r.can_update, r.can_delete, r.can_export, r.scope) for r in rows})
    _cache[key] = (time.monotonic(), matrix)
    return matrix


_share_cache: dict[tuple[str, str], tuple[float, list]] = {}  # (tenant, role)


def invalidate_shares() -> None:
    _share_cache.clear()


async def load_shares(db: AsyncSession, role: str) -> list[list[dict]]:
    """Criteria of the active account sharing rules that include ``role``."""
    from app.models import SharingRule

    key = (tenancy.slug(), role)
    hit = _share_cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    rules = (await db.execute(select(SharingRule).where(SharingRule.active.is_(True)))).scalars().all()
    out = [list(r.criteria or []) for r in rules if role in (r.roles or [])]
    _share_cache[key] = (time.monotonic(), out)
    return out


async def principal_for(db: AsyncSession, user: User) -> Principal:
    """The principal a user acts as: role permissions plus the sharing rules that apply to the role. Also brings
    the live field catalogue (custom fields, objects, field security) up to date for this request."""
    from app.services import reporting

    await reporting.refresh_custom_fields(db)
    return Principal(user, await load_matrix(db, user.role), await load_shares(db, user.role))


async def get_principal(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Principal:
    from app.core.context import current_principal

    if user.role == "partner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Partner accounts can only use the partner portal")
    p = await principal_for(db, user)
    current_principal.set(p)
    return p


def authorize(resource: str, action: str = "read"):
    """Dependency factory: ``Depends(authorize("deals", "update"))`` -> Principal."""
    assert resource in RESOURCES and action in ACTIONS

    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(resource, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role cannot {action} {resource}")
        return principal

    return _dep


def authorize_person(resource: str, action: str = "read"):
    """Like ``authorize`` but refuses API keys: identity, access control and key management need a person's session."""
    check = authorize(resource, action)

    async def _dep(request: Request, principal: Principal = Depends(check)) -> Principal:
        if getattr(request.state, "api_key_id", None):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "API keys can't manage users, permissions, sign-in security or keys")
        return principal

    return _dep


async def seed_permissions(db: AsyncSession) -> None:
    for role in ROLES:
        for resource in RESOURCES:
            spec = DEFAULT_MATRIX.get(role, {}).get(resource, _p("", "own"))
            existing = await db.get(RolePermission, (role, resource))
            if existing is None:
                db.add(RolePermission(role=role, resource=resource, **spec))
    invalidate_cache()
