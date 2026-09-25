from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rbac import ACTIONS, RESOURCES, load_matrix
from app.core.security import create_access_token, verify_password
from app.models import Partner, User
from app.schemas.crm import LoginRequest, TokenResponse

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(func.lower(User.email) == body.username.strip().lower()))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    log_action(db, "login", "users", user.id)
    await db.commit()
    return TokenResponse(access_token=create_access_token(str(user.id), user.role))


@router.get("/users/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    matrix = await load_matrix(db, user.role)
    partner = await db.get(Partner, user.partner_id) if user.partner_id else None
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "manager_id": user.manager_id,
        "partner": {"id": partner.id, "name": partner.name, "tier": partner.tier} if partner else None,
        "permissions": {r: {a: matrix[r].allows(a) for a in ACTIONS} | {"scope": matrix[r].scope} for r in RESOURCES if r in matrix},
    }


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    users = (await db.execute(select(User).where(User.is_active.is_(True), User.role != "partner").order_by(User.full_name))).scalars().all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role} for u in users]
