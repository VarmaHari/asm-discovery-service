import uuid
from importlib import import_module

from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.models import Domain, Scan
from app.services.domains import InitialScanScheduler, PendingScanScheduler


class CeleryScanScheduler(InitialScanScheduler):
    def create_pending_scan(self, db: Session, domain: Domain) -> Scan:
        return PendingScanScheduler().create_pending_scan(db, domain)

    def enqueue_scan(self, scan_id: uuid.UUID) -> bool:
        import_module("app.workers.tasks")
        task = celery_app.tasks["app.workers.tasks.run_dns_scan"]
        result = task.apply_async(
            args=[str(scan_id)],
            task_id=f"dns-scan-{scan_id}",
        )
        return bool(result.id)
