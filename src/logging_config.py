"""Structured console logging plus a bounded stream for the local GUI."""

import logging
import threading
from collections import deque
from datetime import datetime, timezone

FATAL_LEVEL = 60
logging.addLevelName(FATAL_LEVEL, "FATAL")
logging.addLevelName(logging.WARNING, "WARN")

_records: deque[dict] = deque(maxlen=2_000)
_sequence = 0
_lock = threading.Lock()


class GuiLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _sequence
        try:
            fields = getattr(record, "fields", {})
            with _lock:
                _sequence += 1
                _records.append(
                    {
                        "sequence": _sequence,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": record.levelname,
                        "logger": record.name,
                        "message": record.getMessage(),
                        "fields": fields if isinstance(fields, dict) else {},
                    }
                )
        except Exception:
            self.handleError(record)


def configure_logging() -> None:
    """Configure readable terminal logs and the GUI stream once."""
    root = logging.getLogger("agents")
    if getattr(root, "_agents_configured", False):
        return
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s · %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(console)
    root.addHandler(GuiLogHandler())
    root.propagate = False
    root._agents_configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"agents.{name}")


def log(logger: logging.Logger, level: str, message: str, **fields) -> None:
    numeric = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "FATAL": FATAL_LEVEL,
    }.get(level.upper(), logging.INFO)
    logger.log(numeric, message, extra={"fields": fields})


def logs_after(sequence: int) -> dict:
    """Return a stable slice of records newer than a sequence number."""
    with _lock:
        records = [item for item in _records if item["sequence"] > sequence]
        latest = _sequence
    return {"records": records, "latest_sequence": latest}
