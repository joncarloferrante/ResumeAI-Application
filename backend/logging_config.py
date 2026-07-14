import logging
from typing import Any

_LOGGING_CONFIGURED = False


class _ModuleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "component", None):
            record.component = record.name
        return True


def configure_logging(level: int | str = logging.INFO) -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.addFilter(_ModuleFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(component)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)

