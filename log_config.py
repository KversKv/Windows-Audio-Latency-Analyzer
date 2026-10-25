import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_directory: Path) -> None:
    log_directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_directory / "application.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[file_handler, console_handler],
        force=True,
    )
