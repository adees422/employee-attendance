from datetime import datetime, date, time, timedelta
from decimal import Decimal
from typing import Optional
from app.config import IST
from app.services.time_utils import ms_to_datetime_utc, round_half_up


def compute_attendance_date(shift_start: str, shift_end: str, punch_in_ms: int) -> str:
    """
    R1: Time zone and attendance date.
    Shift times and attendance dates are IST (UTC+05:30).
    A record's date is the IST calendar date of its punch_in instant.
    Overnight shifts (shift_end <= shift_start): a punch-in whose IST time
    is strictly earlier than shift_end belongs to the shift that started the previous day.
    """
    dt_utc = ms_to_datetime_utc(punch_in_ms)
    dt_ist = dt_utc.astimezone(IST)

    h_end, m_end = map(int, shift_end.split(":"))
    end_time = time(h_end, m_end)

    is_overnight = shift_end <= shift_start
    if is_overnight and dt_ist.time() < end_time:
        return (dt_ist.date() - timedelta(days=1)).isoformat()
    return dt_ist.date().isoformat()


def compute_late_minutes(shift_start: str, attendance_date_str: str, punch_in_dt: Optional[datetime]) -> int:
    """
    R2: Late.
    Grace period is 10 minutes. Late only if punch-in is strictly more than 10:00
    after shift_start; then late_minutes = whole minutes (floor) since shift_start.
    """
    if not punch_in_dt:
        return 0

    punch_in_ist = punch_in_dt.astimezone(IST)
    sh, sm = map(int, shift_start.split(":"))
    y, m, d = map(int, attendance_date_str.split("-"))
    start_dt_ist = datetime(y, m, d, sh, sm, 0, tzinfo=IST)

    diff_seconds = (punch_in_ist - start_dt_ist).total_seconds()
    # Grace period: strictly more than 10:00 (600 seconds)
    if diff_seconds > 600:
        return int(diff_seconds // 60)
    return 0


def compute_overtime(shift_start: str, shift_end: str, attendance_date_str: str, punch_out_dt: Optional[datetime]) -> int:
    """
    R3: Overtime.
    Whole minutes (floor) between shift_end and punch_out, counted only when >= 30; otherwise 0.
    For overnight shifts (shift_end <= shift_start), shift_end falls on next calendar day.
    """
    if not punch_out_dt:
        return 0

    punch_out_ist = punch_out_dt.astimezone(IST)
    eh, em = map(int, shift_end.split(":"))
    y, m, d = map(int, attendance_date_str.split("-"))

    is_overnight = shift_end <= shift_start
    shift_end_date = date(y, m, d) + (timedelta(days=1) if is_overnight else timedelta(days=0))
    shift_end_dt_ist = datetime(shift_end_date.year, shift_end_date.month, shift_end_date.day, eh, em, 0, tzinfo=IST)

    diff_seconds = (punch_out_ist - shift_end_dt_ist).total_seconds()
    if diff_seconds < 0:
        return 0
    mins = int(diff_seconds // 60)
    return mins if mins >= 30 else 0


def compute_work_hours(punch_in_dt: Optional[datetime], punch_out_dt: Optional[datetime]) -> Optional[float]:
    """
    R4: Work hours.
    (punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up.
    """
    if punch_in_dt is None or punch_out_dt is None:
        return None
    sec = Decimal(str(int((punch_out_dt - punch_in_dt).total_seconds())))
    hours = sec / Decimal("3600")
    rounded = round_half_up(hours, 2)
    return rounded if rounded is not None else 0.0


def is_half_day(work_hours: Optional[float]) -> bool:
    """
    R5: Half day.
    After punch-out, half_day is true when the rounded work_hours < 4.50.
    """
    if work_hours is None:
        return False
    return work_hours < 4.50
