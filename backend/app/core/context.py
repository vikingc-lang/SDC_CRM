"""Request-scoped context: the signed-in principal, for code that has no direct access to it (custom field
security in serializers and value validation). Unset in background jobs, which act as the system."""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.rbac import Principal

current_principal: ContextVar[Any] = ContextVar("current_principal", default=None)


def principal() -> "Principal | None":
    return current_principal.get()


def role() -> str | None:
    p = current_principal.get()
    return p.user.role if p is not None else None
