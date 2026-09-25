"""Request/response schemas for the CRM domain."""
from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.ai import BuyingRole

Tier = Literal["SMB", "Mid-Market", "Enterprise"]
LossReason = Literal["price", "competitor", "no_decision", "timing", "product_fit", "other"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users -----------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: UUID
    email: str
    full_name: str
    role: str


class UserBrief(ORM):
    id: UUID
    full_name: str


# ---- accounts ---------------------------------------------------------------
class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=3, max_length=255)
    industry: Optional[str] = None
    tier: Tier = "Mid-Market"
    owner_id: Optional[UUID] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    tier: Optional[Tier] = None
    owner_id: Optional[UUID] = None


class AccountListItem(BaseModel):
    id: UUID
    name: str
    domain: str
    industry: Optional[str]
    tier: str
    health: int
    owner: Optional[UserBrief]
    open_pipeline: float
    open_deals: int
    contacts: int
    last_activity_at: Optional[datetime]


class AccountOut(ORM):
    id: UUID
    name: str
    domain: str
    industry: Optional[str]
    tier: str
    health_score: int
    owner: Optional[UserBrief]
    custom_metadata: dict
    created_at: datetime


# ---- contacts ---------------------------------------------------------------
class ContactCreate(BaseModel):
    account_id: UUID
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    buying_role: BuyingRole = "Evaluator"


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    buying_role: Optional[BuyingRole] = None


class ContactOut(ORM):
    id: UUID
    account_id: UUID
    first_name: str
    last_name: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    job_title: Optional[str]
    buying_role: str
    account_name: Optional[str] = None


# ---- pipeline / deals -------------------------------------------------------
class StageOut(ORM):
    id: UUID
    name: str
    stage_order: int
    default_probability: int
    is_closed_won: bool
    is_closed_lost: bool


class PipelineOut(ORM):
    id: UUID
    name: str
    is_default: bool
    stages: list[StageOut]


class DealCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    account_id: UUID
    amount: float = Field(default=0, ge=0)
    stage_id: Optional[UUID] = None
    primary_contact_id: Optional[UUID] = None
    target_close_date: Optional[date] = None
    owner_id: Optional[UUID] = None


class DealUpdate(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    primary_contact_id: Optional[UUID] = None
    target_close_date: Optional[date] = None
    owner_id: Optional[UUID] = None


class StageChangeRequest(BaseModel):
    stage_id: UUID
    loss_reason: Optional[LossReason] = None
    override_gates: bool = False


class DealCard(BaseModel):
    id: UUID
    title: str
    amount: float
    currency: str
    account: dict
    stage: str
    stage_id: UUID
    probability: int
    risk_score: int
    risk_factors: dict
    weighted_value: float
    target_close_date: Optional[date]
    days_in_stage: int
    owner: Optional[UserBrief]
    primary_contact: Optional[dict]
    loss_reason: Optional[str]
    ai_insights: dict
    created_at: datetime


class GateCheck(BaseModel):
    criterion: str
    met: bool


class StageChangeResponse(BaseModel):
    deal: DealCard
    forecast_delta: float
    gates: list[GateCheck]
    triggered_action: Optional[str]


# ---- activities / tasks -----------------------------------------------------
class ActivityCreate(BaseModel):
    account_id: UUID
    deal_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    activity_type: Literal["meeting", "call", "note", "email"] = "note"
    summary: str = Field(min_length=1)
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    occurred_at: Optional[datetime] = None


class ActivityOut(BaseModel):
    id: UUID
    date: datetime
    type: str
    summary: str
    sentiment: str
    account: Optional[dict] = None
    deal: Optional[dict] = None
    user: Optional[UserBrief] = None
    similarity: Optional[float] = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_date: Optional[date] = None
    account_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    due_date: Optional[date] = None
    completed: Optional[bool] = None


class TaskOut(BaseModel):
    id: UUID
    title: str
    due_date: Optional[date]
    completed: bool
    source: str
    account: Optional[dict]
    deal: Optional[dict]
    created_at: datetime
