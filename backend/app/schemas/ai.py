"""Pydantic v2 extraction contracts (Functional Solution Specification section 9).

The LLM's JSON response is validated strictly through these models before any
database write happens.
"""
from datetime import date
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

BuyingRole = Literal["Champion", "Decision Maker", "Economic Buyer", "Blocker", "Evaluator", "Influencer", "Legal Counsel", "Procurement"]
Sentiment = Literal["positive", "neutral", "negative"]
ActivityType = Literal["meeting", "call", "note", "email"]
SuggestedStage = Literal["Discovery", "Pain Fit", "Solution Demo", "Proposal/InfoSec"]


class ContactExtracted(BaseModel):
    first_name: str
    last_name: str = ""
    job_title: Optional[str] = None
    email: Optional[EmailStr] = None
    buying_role: BuyingRole = "Evaluator"

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email(cls, v):
        return v or None

    @field_validator("buying_role", mode="before")
    @classmethod
    def _coerce_role(cls, v):
        allowed = {r.lower(): r for r in BuyingRole.__args__}
        return allowed.get(str(v or "").strip().lower(), "Evaluator")


class DealExtracted(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = None
    target_close_date: Optional[date] = None
    suggested_stage: Optional[SuggestedStage] = None

    @field_validator("suggested_stage", mode="before")
    @classmethod
    def _coerce_stage(cls, v):
        allowed = {s.lower(): s for s in SuggestedStage.__args__}
        return allowed.get(str(v or "").strip().lower())


class ActionItem(BaseModel):
    task: str
    due_date: Optional[date] = None


class QuickLogResponse(BaseModel):
    account_name: Optional[str] = None
    domain: Optional[str] = None
    contacts: List[ContactExtracted] = []
    deal: Optional[DealExtracted] = None
    activity_type: ActivityType = "note"
    summary: str
    action_items: List[ActionItem] = []
    sentiment: Sentiment = "neutral"
    # Enrichment (not part of the LLM contract): what already exists in the CRM.
    matched_account_id: Optional[UUID] = None
    matched_deal_id: Optional[UUID] = None
    engine: str = "heuristic"
    signals: dict = Field(default_factory=dict)


class QuickLogRequest(BaseModel):
    raw_text: str = Field(min_length=3, max_length=20000)
    account_id: Optional[UUID] = None


class CommitLogRequest(QuickLogResponse):
    raw_text: Optional[str] = None
    account_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    create_deal: bool = True


class CommitLogResponse(BaseModel):
    status: Literal["committed"] = "committed"
    account_id: UUID
    deal_id: Optional[UUID] = None
    activity_id: UUID
    contacts_created: int = 0
    tasks_created: int = 0


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    account_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
