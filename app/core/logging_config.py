import json
import logging
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    _FIELDS = (
        "event",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "user_id",
        "role",
        "outcome",
        "error_type",
        "scan_id",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self._FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["exception_type"] = exception_type.__name__
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    handler = next(
        (
            existing
            for existing in root_logger.handlers
            if isinstance(existing, logging.StreamHandler)
            and type(existing).__name__ != "LogCaptureHandler"
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        root_logger.addHandler(handler)
    handler.setFormatter(StructuredFormatter())
