"""In-app notifications and the integration outbox (pillars 5, 8)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationEvent, Notification

# Which neighbouring SDC Solutions modules care about which CRM events.
EVENT_TARGETS = {
    "account.updated": ["promo", "yield", "deduct", "nexora"],
    "deal.closed_won": ["promo", "yield", "nexora"],
    "deal.closed_lost": ["nexora"],
    "quote.approved": ["yield"],
    "contract.created": ["deduct", "yield", "nexora"],
    "contract.renewal_opened": ["yield"],
    "invoice.overdue": ["deduct"],
}


def notify(db: AsyncSession, user_ids, kind: str, title: str, body: str | None = None, link: str | None = None) -> None:
    for uid in {u for u in user_ids if u}:
        db.add(Notification(user_id=uid, kind=kind, title=title[:300], body=body, link=link))


def emit(db: AsyncSession, event_type: str, entity_type: str, entity_id: uuid.UUID | None, payload: dict) -> None:
    db.add(IntegrationEvent(event_type=event_type, entity_type=entity_type, entity_id=entity_id,
                            payload=payload, targets=EVENT_TARGETS.get(event_type, ["nexora"])))
