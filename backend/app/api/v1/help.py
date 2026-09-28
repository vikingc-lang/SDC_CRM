"""Help center for business users: role guides, functional areas, process maps, data model, search and
page-aware help. Available to every signed-in person; the content adapts to their role and permissions."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, get_principal
from app.help import content
from app.schemas.ai import AskRequest
from app.services import help as svc
from app.services import insights

router = APIRouter(prefix="/help", tags=["help"])


@router.get("")
async def overview(p: Principal = Depends(get_principal)):
    return {**svc.catalog(p.user.role), "permissions": svc.permissions(p.matrix)}


@router.get("/areas/{key}")
async def area(key: str, p: Principal = Depends(get_principal)):
    found = next((a for a in content.AREAS if a["key"] == key), None)
    if found is None:
        raise HTTPException(404, "No help article with that name")
    return svc.area_out(found)


@router.get("/search")
async def search(q: str, p: Principal = Depends(get_principal)):
    return {"results": svc.search(q, p.user.role, limit=12)}


@router.get("/context")
async def page_context(path: str = "/", p: Principal = Depends(get_principal)):
    return svc.context(path, p.user.role)


@router.get("/data-model")
async def data_model(db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    return await svc.data_model(db, p.user.role)


@router.post("/ask")
async def ask(body: AskRequest, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    """Aiden, answering from the help center only (open to every role, no CRM data involved)."""
    return await insights.ask(db, body.question, principal=p, mode="help", page=body.page)
