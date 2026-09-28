"""Tenant workspaces: create, list, suspend, resume and migrate (core/tenancy.py).

    python -m app.tenants create acme --name "Acme Corp" --host acme.cirra.example \\
        --admin-email admin@acme.example --admin-name "Ada Admin" [--admin-password …] [--max-users 50]
    python -m app.tenants list
    python -m app.tenants suspend acme | resume acme
    python -m app.tenants migrate            # every tenant database to the latest schema (after an upgrade)
    python -m app.tenants set-hosts acme --host crm.acme.example --host acme.cirra.example
    python -m app.tenants set-limit acme --max-users 100   (0 = unlimited)

``create`` makes the tenant's database on the same server, applies every migration, installs the standard
permissions, pipelines, currencies and tax rates, creates the first administrator and registers the tenant.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from datetime import date

from sqlalchemy import create_engine, select, text

from app.core import tenancy
from app.core.config import settings


class ProvisionError(RuntimeError):
    pass


def _alembic_upgrade(db_name: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.attributes["url"] = tenancy.url_for(db_name, sync=True)
    command.upgrade(cfg, "head")


def create_database(db_name: str, *, replace: bool = False) -> None:
    admin = create_engine(settings.sync_database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}).first()
            if exists and not replace:
                raise ProvisionError(f"Database {db_name} already exists")
            if exists:
                conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin.dispose()


async def _bootstrap(admin_email: str, admin_name: str, password: str) -> None:
    from app.core.database import SessionLocal
    from app.core.rbac import seed_permissions
    from app.core.security import hash_password
    from app.models import FxRate, FxRateHistory, Pipeline, PipelineStage, TaxRate, User
    from app.services import fx, tax
    from app.services.pipeline_templates import PIPELINES

    async with SessionLocal() as db:
        await seed_permissions(db)
        for cur, rate in fx.DEFAULT_RATES.items():
            db.add(FxRate(currency=cur, rate_to_usd=rate))
            db.add(FxRateHistory(currency=cur, effective_date=date(2000, 1, 1), rate_to_usd=rate, source="initial"))
        for name, country, region, code, rate in tax.DEFAULT_RATES:
            db.add(TaxRate(name=name, country=country, region=region, tax_code=code, rate=rate))
        spec = next(s for s in PIPELINES if s["is_default"])
        p = Pipeline(name=spec["name"], kind=spec["kind"], is_default=True, description=spec["description"])
        db.add(p)
        await db.flush()
        for order, (name, prob, rules) in enumerate(spec["stages"], start=1):
            db.add(PipelineStage(pipeline_id=p.id, name=name, stage_order=order, default_probability=prob, gate_rules=rules,
                                 is_closed_won=name == "Closed-Won", is_closed_lost=name == "Closed-Lost"))
        db.add(User(email=admin_email.lower(), full_name=admin_name, role="super_admin", password_hash=hash_password(password)))
        await db.commit()


async def create(slug: str, name: str, hosts: list[str], admin_email: str, admin_name: str, admin_password: str | None = None,
                 max_users: int | None = None, *, replace_database: bool = False) -> dict:
    from app.core.database import SessionLocal
    from app.models import Tenant

    if not tenancy.SLUG.match(slug) or slug == tenancy.DEFAULT:
        raise ProvisionError("A workspace id is 3–40 lowercase letters, digits and hyphens, starting with a letter")
    async with SessionLocal() as db:  # the registry lives in the primary database
        if await db.get(Tenant, slug):
            raise ProvisionError(f"Workspace {slug} already exists")
        taken = {h.lower() for t in (await db.execute(select(Tenant))).scalars() for h in (t.hosts or [])}
        clash = taken & {h.lower() for h in hosts}
        if clash:
            raise ProvisionError(f"Host already used by another workspace: {', '.join(sorted(clash))}")
    db_name = tenancy.db_name_for(slug)
    create_database(db_name, replace=replace_database)
    await asyncio.to_thread(_alembic_upgrade, db_name)
    async with SessionLocal() as db:
        db.add(Tenant(slug=slug, name=name, db_name=db_name, hosts=[h.lower() for h in hosts], status="active", max_users=max_users))
        await db.commit()
    tenancy.invalidate()
    password = admin_password or secrets.token_urlsafe(12)
    async with tenancy.use(slug):
        await _bootstrap(admin_email, admin_name, password)
    return {"slug": slug, "database": db_name, "admin_email": admin_email.lower(),
            "temporary_password": None if admin_password else password}


async def set_fields(slug: str, **values) -> None:
    from app.core.database import SessionLocal
    from app.models import Tenant

    async with SessionLocal() as db:
        t = await db.get(Tenant, slug)
        if t is None:
            raise ProvisionError(f"No workspace {slug}")
        for k, v in values.items():
            setattr(t, k, v)
        await db.commit()
    tenancy.invalidate()


async def listing() -> list[dict]:
    from app.core.database import SessionLocal
    from app.models import Tenant

    async with SessionLocal() as db:
        return [{"slug": t.slug, "name": t.name, "database": t.db_name, "hosts": t.hosts, "status": t.status, "max_users": t.max_users}
                for t in (await db.execute(select(Tenant).order_by(Tenant.slug))).scalars()]


async def migrate_all() -> list[str]:
    done = []
    for t in await listing():
        await asyncio.to_thread(_alembic_upgrade, t["database"])
        done.append(t["slug"])
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("slug")
    c.add_argument("--name", required=True)
    c.add_argument("--host", action="append", default=[])
    c.add_argument("--admin-email", required=True)
    c.add_argument("--admin-name", default="Administrator")
    c.add_argument("--admin-password")
    c.add_argument("--max-users", type=int)
    sub.add_parser("list")
    sub.add_parser("migrate")
    for cmd in ("suspend", "resume"):
        sub.add_parser(cmd).add_argument("slug")
    h = sub.add_parser("set-hosts")
    h.add_argument("slug")
    h.add_argument("--host", action="append", default=[])
    lim = sub.add_parser("set-limit")
    lim.add_argument("slug")
    lim.add_argument("--max-users", type=int, required=True)
    a = parser.parse_args()
    if a.cmd == "create":
        print(asyncio.run(create(a.slug, a.name, a.host, a.admin_email, a.admin_name, a.admin_password, a.max_users)))
    elif a.cmd == "list":
        for t in asyncio.run(listing()):
            print(t)
    elif a.cmd == "migrate":
        print("Migrated:", ", ".join(asyncio.run(migrate_all())) or "no tenants")
    elif a.cmd in ("suspend", "resume"):
        asyncio.run(set_fields(a.slug, status="suspended" if a.cmd == "suspend" else "active"))
    elif a.cmd == "set-hosts":
        asyncio.run(set_fields(a.slug, hosts=[x.lower() for x in a.host]))
    elif a.cmd == "set-limit":
        asyncio.run(set_fields(a.slug, max_users=a.max_users or None))


if __name__ == "__main__":
    main()
