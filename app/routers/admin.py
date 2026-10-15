from datetime import date
import json
from typing import Any, Dict, Optional
from bson import json_util
from fastapi import APIRouter, HTTPException, Path, Query


from app.database import db
from app.schemas.common import StatusType
from app.pipelines.employee_monthly import build_employee_monthly_pipeline
from app.pipelines.department_summary import build_department_summary_pipeline
from app.pipelines.late_leaderboard import build_late_leaderboard_pipeline
from app.pipelines.department_trend import build_department_trend_pipeline

router = APIRouter(tags=["admin"])


@router.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(...),
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status_filter: Optional[StatusType] = Query(None, alias="status"),
    from_date: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    valid_endpoints = ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]
    if endpoint not in valid_endpoints:
        raise HTTPException(status_code=422, detail=f"Invalid endpoint for explain. Must be one of {valid_endpoints}")

    collection: str = ""
    raw_explain: Dict[str, Any] = {}

    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from cannot be greater than date_to")
        q: Dict[str, Any] = {}
        if emp_code:
            q["emp_code"] = emp_code
        if status_filter:
            q["status"] = status_filter
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        skip = (page - 1) * page_size
        collection = "attendance_logs"
        raw_explain = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip,
                "limit": page_size
            },
            "verbosity": "executionStats"
        })

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(status_code=422, detail="emp_code and month are required for employee_monthly explain")
        collection = "attendance_logs"
        pipeline = build_employee_monthly_pipeline(emp_code, month)
        raw_explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required for department_summary explain")
        collection = "employees"
        pipeline = build_department_summary_pipeline(month, department)
        raw_explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required for late_leaderboard explain")
        collection = "attendance_logs"
        pipeline = build_late_leaderboard_pipeline(month, limit, department)
        raw_explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(status_code=422, detail="department, from and to are required for department_trend explain")
        from_d = date.fromisoformat(from_date)
        to_d = date.fromisoformat(to_date)
        if to_d < from_d:
            raise HTTPException(status_code=422, detail="to cannot be earlier than from")
        if (to_d - from_d).days + 1 > 92:
            raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

        collection = "employees"
        pipeline = build_department_trend_pipeline(department, from_date, to_date)
        raw_explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })

    # Return pure JSON-serializable explain object
    sanitized_explain = json.loads(json_util.dumps(raw_explain))
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": sanitized_explain
    }
