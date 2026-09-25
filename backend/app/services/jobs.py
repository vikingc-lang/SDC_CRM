"""Background job dispatch.

With ``USE_CELERY=true`` jobs are published to the Celery/Redis queue and run
by ``celery -A app.worker worker``. Otherwise they run in-process after the
HTTP response is sent (FastAPI BackgroundTasks), which keeps local development
dependency-free.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import BackgroundTasks

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import insights, scoring

log = logging.getLogger(__name__)


async def job_embed_activity(activity_id: str) -> None:
    async with SessionLocal() as db:
        await insights.embed_activity(db, uuid.UUID(activity_id))


async def job_rescore_account(account_id: str) -> None:
    async with SessionLocal() as db:
        await scoring.rescore_account(db, uuid.UUID(account_id))
        await db.commit()


async def job_stage_trigger(deal_id: str, user_id: str | None = None) -> None:
    async with SessionLocal() as db:
        await insights.run_stage_trigger(db, uuid.UUID(deal_id), uuid.UUID(user_id) if user_id else None)


async def job_risk_scan() -> None:
    async with SessionLocal() as db:
        await insights.scan_pipeline(db)


async def job_escalations() -> None:
    from app.services import sla

    async with SessionLocal() as db:
        await sla.escalate_overdue(db)


async def job_renewals() -> None:
    from app.services import clm

    async with SessionLocal() as db:
        await clm.run_renewals(db)
        await db.commit()


async def job_rescore_all() -> None:
    async with SessionLocal() as db:
        await scoring.rescore_all(db)


async def job_auto_dedup() -> None:
    from app.services import dedup

    async with SessionLocal() as db:
        await dedup.auto_merge(db)


async def job_erp_sync() -> None:
    from app.services import erp

    async with SessionLocal() as db:
        await erp.sync_inbound(db)
        await db.commit()
        await erp.deliver_events(db)


async def job_mail_sync() -> None:
    from sqlalchemy import select

    from app.models import MailboxConnection
    from app.services import mail

    async with SessionLocal() as db:
        for conn in (await db.execute(select(MailboxConnection).where(MailboxConnection.status != "disabled"))).scalars().all():
            await mail.sync_mailbox(db, conn)


async def job_reindex() -> None:
    async with SessionLocal() as db:
        await insights.reindex(db)


JOBS = {
    "embed_activity": job_embed_activity,
    "rescore_account": job_rescore_account,
    "stage_trigger": job_stage_trigger,
    # scheduled (Celery beat)
    "risk_scan": job_risk_scan,
    "escalations": job_escalations,
    "renewals": job_renewals,
    "rescore_all": job_rescore_all,
    "auto_dedup": job_auto_dedup,
    "erp_sync": job_erp_sync,
    "mail_sync": job_mail_sync,
    "reindex": job_reindex,
}


async def _run_safely(name: str, *args: str | None) -> None:
    try:
        await JOBS[name](*args)
    except Exception:  # background work must never crash the API process
        log.exception("Background job %s failed", name)


def enqueue(background: BackgroundTasks, name: str, *args: str | None) -> None:
    if settings.use_celery:
        from app.worker import run_job

        run_job.delay(name, *args)
    else:
        background.add_task(_run_safely, name, *args)
