from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def round_half_up(val: float | Decimal | None, decimals: int = 2) -> Optional[float]:
    """Round a number using standard half-up rounding (R8)."""
    if val is None:
        return None
    d = Decimal(str(val))
    q = Decimal("10") ** -decimals
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


def truncate_millis_to_seconds(ms: int) -> int:
    """Truncate epoch milliseconds to whole seconds (R1)."""
    return (ms // 1000) * 1000


def ms_to_datetime_utc(ms: int) -> datetime:
    """Convert truncated epoch milliseconds to a UTC timezone-aware datetime."""
    sec = ms // 1000
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def datetime_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert a datetime to epoch milliseconds."""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def get_month_date_range(month_str: str) -> tuple[date, date]:
    """Return start date and end date for a YYYY-MM string."""
    y, m = map(int, month_str.split("-"))
    start_d = date(y, m, 1)
    if m == 12:
        end_d = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        end_d = date(y, m + 1, 1) - timedelta(days=1)
    return start_d, end_d


def count_working_days(joined_on_str: str, month_str: str) -> int:
    """
    R7: Working days.
    Monday-Friday, counted from joined_on for mid-month joiners.
    """
    month_start, month_end = get_month_date_range(month_str)
    joined_d = date.fromisoformat(joined_on_str)
    eff_start = max(month_start, joined_d)

    if eff_start > month_end:
        return 0

    count = 0
    curr = eff_start
    while curr <= month_end:
        if curr.weekday() < 5:  # Monday = 0, Friday = 4
            count += 1
        curr += timedelta(days=1)
    return count
