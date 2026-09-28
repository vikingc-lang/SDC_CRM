"""Celery worker + beat: `celery -A app.worker worker --beat --loglevel=info`."""
import asyncio

from celery import Celery
from celery.schedules import crontab

from app.core.config import enforce_secure_settings, settings

enforce_secure_settings(settings)  # workers decrypt stored credentials: same rules as the API

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
    "journeys": _every("journeys", crontab(minute="*/5")),                     # nurture journey steps
    "case-routing": _every("case_routing", crontab()),                         # push waiting cases to free agents, every minute
    "support-mail": _every("support_mail", crontab(minute="*/2")),            # email-to-case from the support mailbox
    "workflow-waits": _every("workflow_waits", crontab(minute="*/5")),        # resume multi-step workflows after a wait
    "workflow-events": _every("workflow_events", crontab()),                  # triggers left queued by a stopped process, every minute
    "ai-housekeeping": _every("ai_housekeeping", crontab(hour=4, minute=30)),  # AI log retention, expire stale agent suggestions
    "calendar-sync": _every("calendar_sync", crontab(minute="*/10")),         # two-way Google / Microsoft calendar sync
    "fx-feed": _every("fx_feed", crontab(hour=17, minute=15)),                 # daily reference exchange rates (when FX_FEED_URL is set)
    "notifications": _every("notifications", crontab()),                      # email, push and Slack delivery of new notifications
    "notification-digests": _every("notification_digests", crontab(minute=0)),  # daily summaries at each person's chosen hour
    "segments": _every("segments", crontab(minute="*/15")),                   # recompute dynamic segments (entered / left)
    "connectors": _every("connectors", crontab()),                            # Slack / Teams posts; Mailchimp and BambooHR hourly
}
# Scheduled jobs carry no workspace and run once in every active tenant; jobs queued by a request carry theirs.


@celery_app.task(name="app.worker.run_job")
def run_job(name: str, *args, tenant: str | None = None):
    from app.services.jobs import run_in_tenants

    asyncio.run(run_in_tenants(name, *args, tenant=tenant))
