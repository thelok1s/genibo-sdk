"""Structured logging for the SDK.

All SDK loggers live under ``genibo``. Wire traffic is logged by
``genibo.link`` at DEBUG level with ``extra`` fields ``event`` (``tx``/``rx``/
``discard``), ``hex`` and ``summary``, so tools can filter or render it.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; structured ``extra`` fields are preserved."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "t": round(record.created, 6),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in vars(record).items():
            if k not in _STANDARD and not k.startswith("_"):
                data[k] = v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else repr(v)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure(level: str = "info", fmt: str = "text", stream: Any = None) -> None:
    """Configure the ``genibo`` logger for command-line use."""
    handler = logging.StreamHandler(stream or sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root = logging.getLogger("genibo")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False
