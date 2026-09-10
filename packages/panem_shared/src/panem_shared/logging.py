"""structlog setup shared by all processes (Spec NFR-13)."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, component: str, level: int = logging.INFO) -> None:
    """Configure structlog to emit structured JSON with a `component` field.

    Call once per process at startup. `tick`, `correlation_id`, and
    `district_id` are bound per-log-call by callers via `logger.bind(...)`,
    not set here.
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(component=component)


def get_logger(**initial_values: object) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(**initial_values)
    return logger
