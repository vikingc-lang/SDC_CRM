"""Admin → Connectors: configure, test, run and remove the pre-built integrations (services/connectors.py), plus the
public Slack slash-command endpoint."""
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize_person
from app.models import Connector
from app.services import connectors as svc

router = APIRouter(prefix="/admin/connectors", tags=["connectors"])
public = APIRouter(prefix="/public", tags=["public"])


class ConnectorIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=120)
    values: dict = Field(default_factory=dict)  # form values: config and secrets together
    active: bool = True


class ConnectorPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    values: dict | None = None  # secrets left empty keep their stored value
    active: bool | None = None


@router.get("/catalog")
async def catalog(_: Principal = Depends(authorize_person("admin", "read"))):
    return {"connectors": [{"kind": k, **v} for k, v in svc.CATALOG.items()],
            "events": [{"key": k, "label": v} for k, v in svc.POSTABLE_EVENTS.items()]}


@router.get("")
async def list_connectors(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize_person("admin", "read"))):
    return [svc.connector_out(c) for c in (await db.execute(select(Connector).order_by(Connector.created_at))).scalars()]


@router.post("", status_code=201)
async def create_connector(body: ConnectorIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    """Saved only after a successful test, so a connector never sits half-configured."""
    try:
        config, secret = svc.split(body.kind, body.values)
        svc.check_required(body.kind, config, secret)
        c = Connector(kind=body.kind, name=body.name.strip(), config=config, active=body.active, created_by=p.id, state={})
        svc.set_secrets(c, secret)
        message = await svc.test(db, c)
    except svc.ConnectorError as e:
        raise HTTPException(422, str(e))
    db.add(c)
    await db.flush()
    log_action(db, "connector_add", "connector", c.id, f"{svc.CATALOG[c.kind]['label']}: {c.name}")
    await db.commit()
    return {**svc.connector_out(c), "message": message}


async def _get(db: AsyncSession, connector_id: uuid.UUID) -> Connector:
    c = await db.get(Connector, connector_id)
    if c is None:
        raise HTTPException(404, "Connector not found")
    return c


@router.patch("/{connector_id}")
async def update_connector(connector_id: uuid.UUID, body: ConnectorPatch, db: AsyncSession = Depends(get_db),
                           _: Principal = Depends(authorize_person("admin", "update"))):
    c = await _get(db, connector_id)
    if body.name is not None:
        c.name = body.name.strip()
    if body.active is not None:
        c.active = body.active
    message = None
    if body.values is not None:
        try:
            config, secret = svc.split(c.kind, body.values)
            secret = {**svc.secrets_of(c), **secret}  # blank secret fields keep the stored ones
            svc.check_required(c.kind, config, secret)
            c.config = config
            svc.set_secrets(c, secret)
            message = await svc.test(db, c)
        except svc.ConnectorError as e:
            await db.rollback()
            raise HTTPException(422, str(e))
    log_action(db, "connector_edit", "connector", c.id, c.name)
    await db.commit()
    return {**svc.connector_out(c), "message": message}


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(connector_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize_person("admin", "update"))):
    c = await _get(db, connector_id)
    log_action(db, "connector_del", "connector", c.id, c.name)
    await db.delete(c)
    await db.commit()


@router.post("/{connector_id}/test")
async def test_connector(connector_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize_person("admin", "update"))):
    c = await _get(db, connector_id)
    try:
        return {"message": await svc.test(db, c)}
    except svc.ConnectorError as e:
        raise HTTPException(422, str(e))


@router.post("/{connector_id}/run")
async def run_connector(connector_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize_person("admin", "update"))):
    c = await _get(db, connector_id)
    out = await svc.run_one(db, c, scheduled=False)
    await db.commit()
    return {**svc.connector_out(c), "result": out}


@public.post("/slack/command")
async def slack_command(request: Request, db: AsyncSession = Depends(get_db)):
    """Slack's /cirra slash command, verified with the connector's signing secret."""
    raw = await request.body()
    c = (await db.execute(select(Connector).where(Connector.kind == "slack", Connector.active.is_(True)))).scalars().first()
    secret = svc.secrets_of(c).get("signing_secret") if c else None
    if not secret or not svc.verify_slack(secret, request.headers.get("x-slack-request-timestamp", ""), raw,
                                          request.headers.get("x-slack-signature", "")):
        raise HTTPException(401, "Invalid Slack signature")
    form = {k: v[0] for k, v in parse_qs(raw.decode(errors="replace")).items()}
    out = await svc.slash_command(db, form)
    await db.commit()
    return out
