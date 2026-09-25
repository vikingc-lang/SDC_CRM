"""Celery worker: `celery -A app.worker worker --loglevel=info`."""
import asyncio

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery("relate", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.beat_schedule = {
    # Recency decays daily, so re-score every account every night.
    "nightly-rescore": {"task": "app.worker.rescore_all", "schedule": crontab(hour=2, minute=0)},
}


@celery_app.task(name="app.worker.run_job")
def run_job(name: str, *args):
    from app.services.jobs import JOBS

    asyncio.run(JOBS[name](*args))


@celery_app.task(name="app.worker.rescore_all")
def rescore_all():
    from app.core.database import SessionLocal
    from app.services.scoring import rescore_all as _rescore_all

    async def _run():
        async with SessionLocal() as db:
            return await _rescore_all(db)

    return asyncio.run(_run())
