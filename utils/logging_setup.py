"""日志配置模块 - Rotating file + console logging."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(
    name: str = "crawl-china",
    log_file: str = None,
    level: str = "INFO",
    max_mb: int = 10,
    backup_count: int = 5,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Always add console handler
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    # Add file handler if log_file specified, or if running in cron mode
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_file, maxBytes=max_mb * 1024 * 1024, backupCount=backup_count
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    # In cron mode, suppress console output
    if os.environ.get("CRON_MODE") and not log_file:
        logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]

    return logger
