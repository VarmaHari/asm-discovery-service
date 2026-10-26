from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "asm_discovery",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=75,
    task_soft_time_limit=60,
    beat_schedule={
        "dispatch-pending-scans": {
            "task": "app.workers.tasks.dispatch_pending_scans",
            "schedule": 30.0,
        },
        "fail-stale-scans": {
            "task": "app.workers.tasks.fail_stale_scans",
            "schedule": 60.0,
        }
    },
)