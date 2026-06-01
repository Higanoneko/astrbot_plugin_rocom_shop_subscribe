from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional


def cn_tz():
    return timezone(timedelta(hours=8))


def merchant_check_times(base: Optional[datetime] = None) -> List[datetime]:
    now = _aware(base or datetime.now(cn_tz()))
    return [
        now.replace(hour=8, minute=1, second=0, microsecond=0),
        now.replace(hour=12, minute=1, second=0, microsecond=0),
        now.replace(hour=16, minute=1, second=0, microsecond=0),
        now.replace(hour=20, minute=1, second=0, microsecond=0),
    ]


def next_merchant_check_time(now: Optional[datetime] = None) -> datetime:
    current = _aware(now or datetime.now(cn_tz()))
    for check_time in merchant_check_times(current):
        if check_time > current:
            return check_time
    return merchant_check_times(current + timedelta(days=1))[0]


def current_merchant_round(now: Optional[datetime] = None) -> dict:
    current = _aware(now or datetime.now(cn_tz()))
    start = current.replace(hour=8, minute=0, second=0, microsecond=0)
    round_index = None
    round_start = None
    round_end = None
    if start <= current < start + timedelta(hours=16):
        delta_seconds = int((current - start).total_seconds())
        round_index = delta_seconds // int(timedelta(hours=4).total_seconds()) + 1
        round_start = start + timedelta(hours=4 * (round_index - 1))
        round_end = round_start + timedelta(hours=4)

    return {
        "date": current.strftime("%Y-%m-%d"),
        "current": round_index,
        "total": 4,
        "round_id": (
            f"{current.strftime('%Y-%m-%d')}-{round_index}"
            if round_index
            else f"{current.strftime('%Y-%m-%d')}-closed"
        ),
        "is_open": round_index is not None,
        "countdown": format_countdown(round_end - current) if round_end else "未开市",
        "start_time": round_start,
        "end_time": round_end,
    }


def format_countdown(delta: Optional[timedelta]) -> str:
    if not delta:
        return "--"
    total = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0 and minutes > 0:
        return f"{hours}小时{minutes}分钟"
    if hours > 0:
        return f"{hours}小时"
    return f"{minutes}分钟"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=cn_tz())
    return value
