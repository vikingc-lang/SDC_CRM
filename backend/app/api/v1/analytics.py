"""Self-service analytics: report builder, saved reports and dashboards."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Dashboard, SavedReport, User
from app.services import reporting
from app.services.reporting import ReportError

router = APIRouter(prefix="/analytics", tags=["analytics"])

PUBLISHERS = ("super_admin", "sales_manager")  # may share reports and dashboards with everyone


class RunIn(BaseModel):
    definition: dict


class DashFilterIn(BaseModel):
    """Dashboard-wide filters applied to a tile: a relative period on the source's natural date and an owner name."""
    period: str | None = None
    owner: str | None = Field(default=None, max_length=150)


class DrillIn(DashFilterIn):
    definition: dict | None = None  # ad-hoc report (omit for a saved one)
    values: list = Field(default_factory=list, max_length=2)  # one value per grouping, as shown in the result


class ReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    definition: dict
    visibility: Literal["private", "shared"] = "private"


class Tile(BaseModel):
    report_id: uuid.UUID
    size: Literal["third", "half", "full"] = "half"


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["private", "shared"] = "private"
    tiles: list[Tile] = Field(default_factory=list, max_length=24)


def _bad(e: ReportError) -> HTTPException:
    return HTTPException(422, str(e))


def _check_visibility(p: Principal, visibility: str) -> None:
    if visibility == "shared" and p.user.role not in PUBLISHERS:
        raise HTTPException(403, "Only Sales Managers and Super Admins can share with the whole team")


def _can_edit(p: Principal, owner_id) -> bool:
    return owner_id == p.id or p.user.role == "super_admin"


def _visible(p: Principal, model):
    return select(model).where(or_(model.owner_id == p.id, model.visibility == "shared"))


async def _names(db: AsyncSession, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.full_name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}


def _report_out(r: SavedReport, p: Principal, names: dict) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "source": r.source, "definition": r.definition,
            "visibility": r.visibility, "owner": names.get(r.owner_id), "can_edit": _can_edit(p, r.owner_id),
            "updated_at": r.updated_at}


async def _report(db: AsyncSession, p: Principal, report_id: uuid.UUID) -> SavedReport:
    r = (await db.execute(_visible(p, SavedReport).where(SavedReport.id == report_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Report not found")
    return r


# ---- builder ---------------------------------------------------------------------------------

@router.get("/sources")
async def sources(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    await reporting.refresh_custom_fields(db)
    return {"sources": reporting.catalogue(p), "periods": list(reporting.RELATIVE), "buckets": list(reporting.BUCKETS),
            "aggregates": list(reporting.AGGS), "can_share": p.user.role in PUBLISHERS, "can_export": p.can("reports", "export")}


@router.post("/run")
async def run(body: RunIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    try:
        return await reporting.run(db, p, body.definition)
    except ReportError as e:
        raise _bad(e)


# ---- saved reports ---------------------------------------------------------------------------

@router.get("/reports")
async def list_reports(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    rows = (await db.execute(_visible(p, SavedReport).order_by(SavedReport.name))).scalars().all()
    await reporting.refresh_custom_fields(db)
    allowed = {s["key"] for s in reporting.catalogue(p)}
    names = await _names(db, [r.owner_id for r in rows])
    return [_report_out(r, p, names) for r in rows if r.source in allowed]


@router.post("/reports", status_code=201)
async def create_report(body: ReportIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    _check_visibility(p, body.visibility)
    await reporting.refresh_custom_fields(db)
    try:
        reporting.validate(body.definition)
    except ReportError as e:
        raise _bad(e)
    r = SavedReport(name=body.name, description=body.description, owner_id=p.id, source=body.definition["source"],
                    definition=body.definition, visibility=body.visibility)
    db.add(r)
    await db.commit()
    return _report_out(r, p, {p.id: p.user.full_name})


@router.get("/reports/{report_id}")
async def get_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    r = await _report(db, p, report_id)
    return _report_out(r, p, await _names(db, [r.owner_id]))


@router.put("/reports/{report_id}")
async def update_report(report_id: uuid.UUID, body: ReportIn, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(authorize("reports", "read"))):
    r = await _report(db, p, report_id)
    if not _can_edit(p, r.owner_id):
        raise HTTPException(403, "Only the report's owner can change it. Save a copy instead.")
    _check_visibility(p, body.visibility)
    await reporting.refresh_custom_fields(db)
    try:
        reporting.validate(body.definition)
    except ReportError as e:
        raise _bad(e)
    r.name, r.description, r.definition, r.visibility, r.source = body.name, body.description, body.definition, body.visibility, body.definition["source"]
    await db.commit()
    return _report_out(r, p, await _names(db, [r.owner_id]))


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    r = await _report(db, p, report_id)
    if not _can_edit(p, r.owner_id):
        raise HTTPException(403, "Only the report's owner can delete it")
    await db.delete(r)
    await db.commit()


@router.post("/reports/{report_id}/run")
async def run_saved(report_id: uuid.UUID, body: DashFilterIn | None = None, db: AsyncSession = Depends(get_db),
                    p: Principal = Depends(authorize("reports", "read"))):
    r = await _report(db, p, report_id)
    try:
        await reporting.refresh_custom_fields(db)
        defn, skipped = reporting.with_dashboard_filters(r.definition, body.period if body else None, body.owner if body else None)
        return {**await reporting.run(db, p, defn), "skipped_filters": skipped}
    except ReportError as e:
        raise _bad(e)


@router.post("/drill")
async def drill(body: DrillIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    """The records behind one bar, slice or summary row of an ad-hoc report."""
    return await _drill(db, p, body.definition or {}, body)


@router.post("/reports/{report_id}/drill")
async def drill_saved(report_id: uuid.UUID, body: DrillIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    r = await _report(db, p, report_id)
    return await _drill(db, p, r.definition, body)


async def _drill(db: AsyncSession, p: Principal, definition: dict, body: DrillIn) -> dict:
    try:
        await reporting.refresh_custom_fields(db)
        defn, _ = reporting.with_dashboard_filters(definition, body.period, body.owner)
        return await reporting.run(db, p, reporting.drill_definition(defn, body.values))
    except ReportError as e:
        raise _bad(e)


@router.get("/reports/{report_id}/export")
async def export_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "export"))):
    r = await _report(db, p, report_id)
    try:
        result = await reporting.run(db, p, {**r.definition, "limit": reporting.MAX_ROWS})
    except ReportError as e:
        raise _bad(e)
    log_action(db, "export", "saved_reports", r.id, f"{result['row_count']} rows")
    await db.commit()
    slug = "".join(ch if ch.isalnum() else "-" for ch in r.name.lower()).strip("-")[:60] or "report"
    return Response(reporting.to_csv(result), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-{date.today().isoformat()}.csv"'})


# ---- dashboards --------------------------------------------------------------------------------

def _dashboard_out(d: Dashboard, p: Principal, names: dict, reports: dict | None = None) -> dict:
    out = {"id": d.id, "name": d.name, "description": d.description, "visibility": d.visibility, "owner": names.get(d.owner_id),
           "can_edit": _can_edit(p, d.owner_id), "tile_count": len(d.tiles or []), "updated_at": d.updated_at}
    if reports is not None:
        out["tiles"] = [{**t, "report": reports.get(t["report_id"])} for t in d.tiles or []]
    return out


async def _dashboard(db: AsyncSession, p: Principal, dashboard_id: uuid.UUID) -> Dashboard:
    d = (await db.execute(_visible(p, Dashboard).where(Dashboard.id == dashboard_id))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Dashboard not found")
    return d


async def _check_tiles(db: AsyncSession, p: Principal, tiles: list[Tile], visibility: str) -> list[dict]:
    ids = [t.report_id for t in tiles]
    found = {r.id: r for r in (await db.execute(_visible(p, SavedReport).where(SavedReport.id.in_(ids)))).scalars()} if ids else {}
    for t in tiles:
        r = found.get(t.report_id)
        if r is None:
            raise HTTPException(422, "A tile refers to a report you can't see")
        if visibility == "shared" and r.visibility != "shared":
            raise HTTPException(422, f"Share the report '{r.name}' before adding it to a shared dashboard")
    return [{"report_id": str(t.report_id), "size": t.size} for t in tiles]


@router.get("/dashboards")
async def list_dashboards(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    rows = (await db.execute(_visible(p, Dashboard).order_by(Dashboard.name))).scalars().all()
    names = await _names(db, [d.owner_id for d in rows])
    return [_dashboard_out(d, p, names) for d in rows]


@router.post("/dashboards", status_code=201)
async def create_dashboard(body: DashboardIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    _check_visibility(p, body.visibility)
    d = Dashboard(name=body.name, description=body.description, owner_id=p.id, visibility=body.visibility,
                  tiles=await _check_tiles(db, p, body.tiles, body.visibility))
    db.add(d)
    await db.commit()
    return _dashboard_out(d, p, {p.id: p.user.full_name})


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    d = await _dashboard(db, p, dashboard_id)
    ids = [uuid.UUID(t["report_id"]) for t in d.tiles or []]
    reps = (await db.execute(_visible(p, SavedReport).where(SavedReport.id.in_(ids)))).scalars().all() if ids else []
    await reporting.refresh_custom_fields(db)
    allowed = {s["key"] for s in reporting.catalogue(p)}
    names = await _names(db, [d.owner_id, *[r.owner_id for r in reps]])
    by_id = {str(r.id): _report_out(r, p, names) for r in reps if r.source in allowed}
    return _dashboard_out(d, p, names, by_id)


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: uuid.UUID, body: DashboardIn, db: AsyncSession = Depends(get_db),
                           p: Principal = Depends(authorize("reports", "read"))):
    d = await _dashboard(db, p, dashboard_id)
    if not _can_edit(p, d.owner_id):
        raise HTTPException(403, "Only the dashboard's owner can change it")
    _check_visibility(p, body.visibility)
    d.name, d.description, d.visibility = body.name, body.description, body.visibility
    d.tiles = await _check_tiles(db, p, body.tiles, body.visibility)
    await db.commit()
    return _dashboard_out(d, p, await _names(db, [d.owner_id]))


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    d = await _dashboard(db, p, dashboard_id)
    if not _can_edit(p, d.owner_id):
        raise HTTPException(403, "Only the dashboard's owner can delete it")
    await db.delete(d)
    await db.commit()
