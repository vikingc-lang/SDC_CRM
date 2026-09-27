"""Celery worker + beat: `celery -A app.worker worker --beat --loglevel=info`."""
import asyncio

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery("cirra", broker=settings.redis_url, backend=settings.redis_url)


def _every(job: str, schedule) -> dict:
    return {"task": "app.worker.run_job", "schedule": schedule, "args": (job,)}


celery_app.conf.beat_schedule = {
    "mail-sync": _every("mail_sync", crontab(minute="*/5")),                   # pillar 5: IMAP ingestion
    "risk-scan": _every("risk_scan", crontab(minute=15)),                      # pillar 6: slippage copilot, hourly
    "sla-escalations": _every("escalations", crontab(minute=30)),              # pillar 5: overdue escalation, hourly
    "erp-sync": _every("erp_sync", crontab(minute=45, hour="*/4")),            # pillar 8: customer master + A/R
    "erp-orders": _every("erp_orders", crontab(minute="*/2")),                # lead-to-order: sales-order push + acks
    "lead-rescore": _every("lead_rescore", crontab(hour=1, minute=30)),       # lead engagement decay
    "nightly-rescore": _every("rescore_all", crontab(hour=2, minute=0)),       # recency decays daily
    "renewals": _every("renewals", crontab(hour=3, minute=0)),                 # pillar 7: 120-day renewal engine
    "auto-dedup": _every("auto_dedup", crontab(hour=3, minute=30)),            # pillar 1: autonomous dedup
    "reindex": _every("reindex", crontab(hour=4, minute=0)),                   # pillar 6: vector memory hygiene
    "workflows": _every("workflows", crontab(minute=5)),                       # scheduled workflow rules, hourly
    "case-sla": _every("case_sla", crontab(minute="*/10")),                   # service: SLA breach alerts
    "territories": _every("territories", crontab(hour=2, minute=30)),          # nightly territory realignment
    "webhooks": _every("webhooks", crontab()),                                 # signed webhook fan-out and retries, every minute
    "report-subscriptions": _every("report_subscriptions", crontab(minute=0)),  # scheduled report deliveries, hourly
}


@celery_app.task(name="app.worker.run_job")
def run_job(name: str, *args):
    from app.services import workflows
    from app.services.jobs import JOBS

    async def _run():
        await JOBS[name](*args)
        await workflows.drain()  # finish automation triggered by this job before the loop closes

    asyncio.run(_run())
