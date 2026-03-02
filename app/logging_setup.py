from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings


class DailyFileHandler(logging.Handler):
    """Write logs to /dir/YYYYMMDD.log and switch file when day changes."""

    def __init__(self, directory: Path, timezone_name: str) -> None:
        super().__init__()
        self._directory = directory
        self._tz = ZoneInfo(timezone_name)
        self._current_date: str | None = None
        self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            target_date = datetime.fromtimestamp(record.created, tz=self._tz).strftime("%Y%m%d")
            message = self.format(record)
            self._write_line(target_date, message)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()

    @property
    def directory(self) -> Path:
        return self._directory

    def _write_line(self, date_key: str, message: str) -> None:
        self.acquire()
        try:
            if self._stream is None or self._current_date != date_key:
                self._rotate(date_key)
            self._stream.write(f"{message}\n")
            self._stream.flush()
        finally:
            self.release()

    def _rotate(self, date_key: str) -> None:
        if self._stream is not None:
            self._stream.close()
        self._directory.mkdir(parents=True, exist_ok=True)
        log_path = self._directory / f"{date_key}.log"
        self._stream = log_path.open(mode="a", encoding="utf-8")
        self._current_date = date_key


def configure_whoop_file_logger(settings: Settings) -> None:
    logger = logging.getLogger("app.whoop_client")
    logger.setLevel(getattr(logging, settings.whoop_http_log_level.upper().strip(), logging.INFO))

    existing = next(
        (
            handler
            for handler in logger.handlers
            if isinstance(handler, DailyFileHandler) and handler.directory == settings.whoop_http_log_file_dir
        ),
        None,
    )
    if existing is not None:
        return

    handler = DailyFileHandler(
        directory=settings.whoop_http_log_file_dir,
        timezone_name=settings.timezone,
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
