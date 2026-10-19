"""Structured logging with redaction applied at emit time.

Log lines are JSON when `log_json` is set (the production default) and a compact
key=value form otherwise, because a human reading a terminal during development
should not have to parse JSON to see what happened.

Two things are guaranteed here and nowhere else:

* Every string that reaches a handler has been through :func:`studio.redact.scrub`.
* Every record carries the request id and, when there is one, the job id, so a
  user-visible failure can be traced without asking them for a timestamp.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

from .redact import scrub, scrub_structure

_CONFIGURED = False
_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}


class RedactingFormatter(logging.Formatter):
    """Renders a record, scrubbing the message and every extra field."""

    def __init__(self, *, json_output: bool, service: str) -> None:
        super().__init__()
        self.json_output = json_output
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        for key in ("request_id", "job_id", "user_id", "workspace_id", "connection_id", "route"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(scrub_structure(extra))  # type: ignore[arg-type]
        if record.exc_info:
            # A traceback can contain a URL with a signed query string. Scrub it.
            payload["exception"] = scrub(self.formatException(record.exc_info))

        if self.json_output:
            return json.dumps(payload, ensure_ascii=False, default=str)
        head = "%s %-5s %s" % (
            time.strftime("%H:%M:%S", time.localtime(payload["ts"])),
            payload["level"].upper()[:5],
            payload["logger"],
        )
        tail = " ".join(
            "%s=%s" % (k, v) for k, v in payload.items() if k not in ("ts", "level", "logger", "message")
        )
        line = "%s %s" % (head, payload["message"])
        if tail:
            line = line + " " + tail
        return line.rstrip()


def configure_logging(
    level: str = "info",
    *,
    json_output: bool = False,
    service: str = "studio",
    stream: Any = None,
) -> None:
    """Install the handler. Idempotent: repeated calls replace the handler."""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingFormatter(json_output=json_output, service=service))
    root.addHandler(handler)
    root.setLevel(_LEVELS.get(level, logging.INFO))
    # urllib logs full URLs at INFO; those can carry signed query strings.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("studio." + name)


class LogContext:
    """Small helper that stamps a logger call with correlation ids."""

    def __init__(self, logger: logging.Logger, **fields: Any) -> None:
        self.logger = logger
        self.fields = fields

    def bind(self, **fields: Any) -> "LogContext":
        merged = dict(self.fields)
        merged.update(fields)
        return LogContext(self.logger, **merged)

    def _emit(self, level: int, message: str, **fields: Any) -> None:
        merged = dict(self.fields)
        merged.update(fields)
        record_fields = {}
        for key in ("request_id", "job_id", "user_id", "workspace_id", "connection_id", "route"):
            if key in merged:
                record_fields[key] = merged.pop(key)
        self.logger.log(
            level,
            message,
            extra={"extra_fields": scrub_structure(merged), **record_fields},
        )

    def debug(self, message: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        merged = dict(self.fields)
        merged.update(fields)
        record_fields = {}
        for key in ("request_id", "job_id", "user_id", "workspace_id", "connection_id", "route"):
            if key in merged:
                record_fields[key] = merged.pop(key)
        self.logger.exception(
            message,
            extra={"extra_fields": scrub_structure(merged), **record_fields},
        )


def logger_for(name: str, **fields: Any) -> LogContext:
    return LogContext(get_logger(name), **fields)
