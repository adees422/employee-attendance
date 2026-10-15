from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query

from app.database import db
from app.services.time_utils import count_working_days, round_half_up
from app.pipelines.employee_monthly import build_employee_monthly_pipeline
from app.pipelines.department_summary import build_department_summary_pipeline
from app.pipelines.late_leaderboard import build_late_leaderboard_pipeline
from app.pipelines.department_trend import build_department_trend_pipeline

router = APIRouter(tags=["analytics"])


@router.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str = Path(...),
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    working_days = count_working_days(emp["joined_on"], month)
    pipeline = build_employee_monthly_pipeline(emp_code, month)
    res = list(db.attendance_logs.aggregate(pipeline))

    if res:
        agg = res[0]
        present_days = round_half_up(agg.get("present_days", 0.0), 2) or 0.0
        leave_days = agg.get("leave_days", 0)
        late_count = agg.get("late_count", 0)
        total_late_minutes = agg.get("total_late_minutes", 0)
        total_overtime_minutes = agg.get("total_overtime_minutes", 0)
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    if working_days == 0:
        attendance_pct = None
    else:
        pct = (Decimal(str(present_days)) / Decimal(str(working_days))) * Decimal("100")
        attendance_pct = round_half_up(pct, 2)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@router.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None
):
    pipeline = build_department_summary_pipeline(month, department)
    results = list(db.employees.aggregate(pipeline))

    items = []
    for r in results:
        avg_wh = r.get("avg_work_hours")
        items.append({
            "department": r["department"],
            "headcount": r["headcount"],
            "present_days": round_half_up(r.get("present_days", 0.0), 2),
            "avg_work_hours": round_half_up(avg_wh, 2) if avg_wh is not None else None,
            "late_count": r.get("late_count", 0),
            "total_late_minutes": r.get("total_late_minutes", 0),
            "leave_count": r.get("leave_count", 0),
            "on_duty_count": r.get("on_duty_count", 0),
        })

    return {
        "month": month,
        "items": items
    }


@router.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@router.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str = Path(...),
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
):
    from_d = date.fromisoformat(from_date)
    to_d = date.fromisoformat(to_date)

    if to_d < from_d:
        raise HTTPException(status_code=422, detail="to cannot be earlier than from")

    if (to_d - from_d).days + 1 > 92:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    rows = list(db.employees.aggregate(pipeline))

    items = []
    for r in rows:
        att_rate = r.get("attendance_rate")
        m_avg = r.get("moving_avg_7d")
        items.append({
            "date": r["date"],
            "is_working_day": r["is_working_day"],
            "headcount": r["headcount"],
            "present_count": round_half_up(r.get("present_count", 0.0), 2),
            "late_count": r.get("late_count", 0),
            "attendance_rate": round_half_up(att_rate, 4) if att_rate is not None else None,
            "moving_avg_7d": round_half_up(m_avg, 4) if m_avg is not None else None,
        })

    return {
        "department": department,
        "items": items
    }
