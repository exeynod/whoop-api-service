from __future__ import annotations

import logging
from datetime import datetime

import pytest

from app.logging_setup import DailyFileHandler


@pytest.mark.unit
def test_daily_file_handler_writes_to_yyyymmdd_file(tmp_path):
    handler = DailyFileHandler(directory=tmp_path, timezone_name="Europe/Moscow")
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test.daily_file_handler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    logger.info("first line")

    files = list(tmp_path.glob("*.log"))
    assert files
    assert files[0].name.endswith(".log")

    content = files[0].read_text(encoding="utf-8")
    assert "first line" in content


@pytest.mark.unit
def test_daily_file_handler_rotates_by_record_date(tmp_path):
    handler = DailyFileHandler(directory=tmp_path, timezone_name="UTC")
    handler.setFormatter(logging.Formatter("%(message)s"))

    record_day1 = logging.LogRecord(
        name="test.daily",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="day1",
        args=(),
        exc_info=None,
    )
    record_day1.created = datetime(2026, 3, 1, 10, 0).timestamp()

    record_day2 = logging.LogRecord(
        name="test.daily",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="day2",
        args=(),
        exc_info=None,
    )
    record_day2.created = datetime(2026, 3, 2, 10, 0).timestamp()

    handler.emit(record_day1)
    handler.emit(record_day2)
    handler.close()

    day1_path = tmp_path / "20260301.log"
    day2_path = tmp_path / "20260302.log"

    assert day1_path.exists()
    assert day2_path.exists()
    assert "day1" in day1_path.read_text(encoding="utf-8")
    assert "day2" in day2_path.read_text(encoding="utf-8")
