"""Saved list views: the columns, filters and sort a user (or the team) looks at a list through.

Views run on the report engine in list mode, so every field the report builder offers - custom fields
included - can be a column or a filter, and the caller's row-level scope always applies. Columns listed
in ``bulk.INLINE`` can be edited in place through the bulk endpoint with a single id.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, get_principal
from app.models import ListView, User
from app.services import bulk, reporting
from app.services.reporting import ReportError

router = APIRouter(prefix="/views", tags=["views"])

SOURCE_PATTERN = r"^(leads|accounts|contacts|cases|deals|obj_[a-z][a-z0-9_]{1,30})$"  # the main lists and every custom object
PUBLISHERS = ("super_admin", "sales_manager")  # may share views with everyone, as with reports
MAX_COLUMNS = 12


class Sort(BaseModel):
    by: str | None = None
    dir: Literal["asc", "desc"] = "asc"


class ViewSpec(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    filters: list[dict] = Field(default_factory=list, max_length=20)
    sort: Sort = Field(default_factory=Sort)


class ViewIn(ViewSpec):
    source: str = Field(pattern=SOURCE_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    visibility: Literal["private", "shared"] = "private"


class RunIn(ViewSpec):
    source: str = Field(pattern=SOURCE_PATTERN)
    limit: int = Field(default=200, ge=1, le=reporting.MAX_ROWS)


async def _source(db: AsyncSession, p: Principal, key: str) -> reporting.Source:
    await reporting.refresh_custom_fields(db)
    src = reporting.src_for(key, p)
    if src is None:
        raise HTTPException(404, "No such list")
    if not p.can(src.resource, "read"):
        raise HTTPException(403, f"Your role can't read {src.label.lower()}")
    return src


def _check(src: reporting.Source, spec: ViewSpec) -> None:
    unknown = [c for c in spec.columns if c not in src.fields]
    if unknown:
        raise HTTPException(422, f"Unknown column '{unknown[0]}'")
    if spec.sort.by and spec.sort.by not in src.fields:
        raise HTTPException(422, f"Can't sort by '{spec.sort.by}'")
    try:
        reporting.validate_filters(src.key, spec.filters)
    except ReportError as e:
        raise HTTPException(422, str(e))


def _out(v: ListView, p: Principal, names: dict) -> dict:
    return {"id": v.id, "source": v.source, "name": v.name, "visibility": v.visibility, "columns": v.columns, "filters": v.filters,
            "sort": v.sort, "owner": names.get(v.owner_id), "can_edit": v.owner_id == p.id or p.user.role == "super_admin"}


async def _view(db: AsyncSession, p: Principal, view_id: uuid.UUID, edit: bool = False) -> ListView:
    v = (await db.execute(select(ListView).where(ListView.id == view_id, or_(ListView.owner_id == p.id, ListView.visibility == "shared")))).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "View not found")
    if edit and not (v.owner_id == p.id or p.user.role == "super_admin"):
        raise HTTPException(403, "Only the view's owner can change it. Save a copy instead.")
    return v


@router.get("")
async def list_views(source: str = Query(..., pattern=SOURCE_PATTERN), db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    """The views of one list the caller can use, plus what the view editor needs: the fields and the in-place edits."""
    src = await _source(db, p, source)
    rows = (await db.execute(select(ListView).where(ListView.source == source, or_(ListView.owner_id == p.id, ListView.visibility == "shared"))
                             .order_by(ListView.name))).scalars().all()
    owners = {v.owner_id for v in rows if v.owner_id}
    names = {u.id: u.full_name for u in (await db.execute(select(User).where(User.id.in_(owners)))).scalars()} if owners else {}
    inline = {col: spec for col, spec in bulk.INLINE.get(source, {}).items()} if p.can(src.resource, "update") else {}
    return {"views": [_out(v, p, names) for v in rows], "default_columns": src.default_columns, "inline": inline,
            "can_share": p.user.role in PUBLISHERS, "link": reporting.link_for(source),
            "fields": [{"key": k, "label": f.label, "type": f.type, "options": f.options, "ops": list(reporting.OPS[f.type])}
                       for k, f in src.fields.items()], "periods": list(reporting.RELATIVE)}


@router.post("/run")
async def run_view(body: RunIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    src = await _source(db, p, body.source)
    full = reporting.src_of(body.source)
    # a shared view's columns that are hidden from this role are simply not shown
    columns = [c for c in body.columns if c in src.fields or c not in full.fields] or src.default_columns
    _check(src, body.model_copy(update={"columns": columns, "filters": [], "sort": body.sort if body.sort.by in src.fields else Sort()}))
    defn = {"source": src.key, "columns": columns, "filters": body.filters, "limit": body.limit, "with_ids": True,
            "sort": body.sort.model_dump() if body.sort.by else {}}
    try:
        return await reporting.run(db, p, defn)
    except ReportError as e:
        raise HTTPException(422, str(e))


@router.post("", status_code=201)
async def create_view(body: ViewIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    src = await _source(db, p, body.source)
    _check(src, body)
    if body.visibility == "shared" and p.user.role not in PUBLISHERS:
        raise HTTPException(403, "Only Sales Managers and Super Admins can share views with the whole team")
    v = ListView(source=body.source, name=body.name, owner_id=p.id, visibility=body.visibility, columns=body.columns,
                 filters=body.filters, sort=body.sort.model_dump())
    db.add(v)
    await db.commit()
    return _out(v, p, {p.id: p.user.full_name})


@router.put("/{view_id}")
async def update_view(view_id: uuid.UUID, body: ViewIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    v = await _view(db, p, view_id, edit=True)
    if body.source != v.source:
        raise HTTPException(422, "A view can't move to another list")
    src = await _source(db, p, body.source)
    _check(src, body)
    if body.visibility == "shared" and v.visibility != "shared" and p.user.role not in PUBLISHERS:
        raise HTTPException(403, "Only Sales Managers and Super Admins can share views with the whole team")
    v.name, v.visibility, v.columns, v.filters, v.sort = body.name, body.visibility, body.columns, body.filters, body.sort.model_dump()
    await db.commit()
    await db.refresh(v)
    names = {u.id: u.full_name for u in (await db.execute(select(User).where(User.id == v.owner_id))).scalars()} if v.owner_id else {}
    return _out(v, p, names)


@router.delete("/{view_id}", status_code=204)
async def delete_view(view_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    v = await _view(db, p, view_id, edit=True)
    await db.delete(v)
    await db.commit()
