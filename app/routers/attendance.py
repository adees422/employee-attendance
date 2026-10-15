from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Path, Query, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.database import db
from app.schemas.attendance import PunchInRequest, PunchOutRequest, RegularizeRequest
from app.schemas.common import StatusType
from app.services.time_utils import (
    truncate_millis_to_seconds,
    ms_to_datetime_utc,
)
from app.services.business_rules import (
    compute_attendance_date,
    compute_late_minutes,
    compute_overtime,
    compute_work_hours,
    is_half_day,
)
from app.services.formatters import format_attendance_doc

router = APIRouter(tags=["attendance"])


@router.post("/attendance/punch-in", status_code=status.HTTP_201_CREATED)
def punch_in(body: PunchInRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        punched_at_ms = truncate_millis_to_seconds(body.punched_at)
    else:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        punched_at_ms = truncate_millis_to_seconds(now_ms)

    punch_in_dt = ms_to_datetime_utc(punched_at_ms)
    attendance_date = compute_attendance_date(emp["shift_start"], emp["shift_end"], punched_at_ms)
    late_minutes = compute_late_minutes(emp["shift_start"], attendance_date, punch_in_dt)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punch_in_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this date")

    return format_attendance_doc(doc)


@router.post("/attendance/punch-out", status_code=status.HTTP_200_OK)
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        punched_at_ms = truncate_millis_to_seconds(body.punched_at)
    else:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        punched_at_ms = truncate_millis_to_seconds(now_ms)

    punched_at_dt = ms_to_datetime_utc(punched_at_ms)

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at_dt}},
        sort=[("punch_in", -1)]
    )

    if not record or record.get("punch_in") is None:
        raise HTTPException(status_code=404, detail="No open punch-in found")

    punch_in_dt = record["punch_in"]

    # Validate interval between punch_in and punch_out
    if punched_at_dt <= punch_in_dt:
        raise HTTPException(status_code=422, detail="punch_out must be strictly after punch_in")

    if (punched_at_dt - punch_in_dt).total_seconds() > 86400:
        raise HTTPException(status_code=422, detail="punch_out cannot be more than 24 hours after punch_in")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Record is already punched out")

    work_hours = compute_work_hours(punch_in_dt, punched_at_dt)
    overtime_minutes = compute_overtime(emp["shift_start"], emp["shift_end"], record["date"], punched_at_dt)
    half_day = is_half_day(work_hours)

    updated_doc = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": punched_at_dt,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        },
        return_document=ReturnDocument.AFTER
    )

    if not updated_doc:
        raise HTTPException(status_code=409, detail="Record is already punched out")

    return format_attendance_doc(updated_doc)


@router.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status: Optional[StatusType] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from cannot be greater than date_to")

    q: Dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if status:
        q["status"] = status
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)

    items = [format_attendance_doc(d) for d in cursor]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    body: RegularizeRequest,
    emp_code: str = Path(...),
    date_str: str = Path(..., alias="date", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_str})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found for that date")

    # Status transitions and presence rules
    old_status = record["status"]
    new_status = body.status if body.status is not None else old_status

    target_punch_in: Optional[datetime] = None
    target_punch_out: Optional[datetime] = None

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(status_code=422, detail="Punch times cannot be supplied when setting status to ABSENT or LEAVE")
        target_punch_in = None
        target_punch_out = None
    else:
        # Presence status
        if body.punch_in is not None:
            target_punch_in = ms_to_datetime_utc(truncate_millis_to_seconds(body.punch_in))
        else:
            raw_pi = record.get("punch_in")
            target_punch_in = None if old_status in ("ABSENT", "LEAVE") else (raw_pi if isinstance(raw_pi, datetime) else None)

        if target_punch_in is None:
            raise HTTPException(status_code=422, detail="A presence status requires a punch_in")

        # punch_in must stay on record's attendance date per R1
        punch_in_ms = int(target_punch_in.timestamp() * 1000)
        calc_date = compute_attendance_date(emp["shift_start"], emp["shift_end"], punch_in_ms)
        if calc_date != date_str:
            raise HTTPException(status_code=422, detail="punch_in must belong to the record's attendance date")

        if body.punch_out is not None:
            target_punch_out = ms_to_datetime_utc(truncate_millis_to_seconds(body.punch_out))
        else:
            raw_po = record.get("punch_out")
            target_punch_out = None if old_status in ("ABSENT", "LEAVE") else (raw_po if isinstance(raw_po, datetime) else None)

        if target_punch_out is not None:
            if target_punch_out <= target_punch_in:
                raise HTTPException(status_code=422, detail="punch_out must be strictly after punch_in")
            if (target_punch_out - target_punch_in).total_seconds() > 86400:
                raise HTTPException(status_code=422, detail="punch_out cannot be more than 24 hours after punch_in")

    # Compute derived fields
    if new_status in ("ABSENT", "LEAVE"):
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_late_minutes = compute_late_minutes(emp["shift_start"], date_str, target_punch_in)
        if target_punch_out is not None:
            new_work_hours = compute_work_hours(target_punch_in, target_punch_out)
            new_overtime_minutes = compute_overtime(emp["shift_start"], emp["shift_end"], date_str, target_punch_out)
            new_half_day = is_half_day(new_work_hours)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False

    # Detect actual changes
    changes: Dict[str, Any] = {}

    if old_status != new_status:
        changes["status"] = {"from": old_status, "to": new_status}

    old_punch_in = record.get("punch_in")
    if old_punch_in != target_punch_in:
        changes["punch_in"] = {"from": old_punch_in, "to": target_punch_in}

    old_punch_out = record.get("punch_out")
    if old_punch_out != target_punch_out:
        changes["punch_out"] = {"from": old_punch_out, "to": target_punch_out}

    old_work_hours = record.get("work_hours")
    if old_work_hours != new_work_hours:
        changes["work_hours"] = {"from": old_work_hours, "to": new_work_hours}

    old_late_minutes = record.get("late_minutes", 0)
    if old_late_minutes != new_late_minutes:
        changes["late_minutes"] = {"from": old_late_minutes, "to": new_late_minutes}

    old_overtime_minutes = record.get("overtime_minutes", 0)
    if old_overtime_minutes != new_overtime_minutes:
        changes["overtime_minutes"] = {"from": old_overtime_minutes, "to": new_overtime_minutes}

    old_half_day = record.get("half_day", False)
    if old_half_day != new_half_day:
        changes["half_day"] = {"from": old_half_day, "to": new_half_day}

    if not changes:
        raise HTTPException(status_code=422, detail="A request that changes nothing is 422")

    existing_history = record.get("history", [])
    history_len = len(existing_history)

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    history_entry = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Optimistic locking: check existing history size to prevent lost updates
    filter_q: Dict[str, Any] = {"_id": record["_id"]}
    if history_len == 0:
        filter_q["$or"] = [{"history": {"$size": 0}}, {"history": {"$exists": False}}]
    else:
        filter_q["history"] = {"$size": history_len}

    updated_record = db.attendance_logs.find_one_and_update(
        filter_q,
        {
            "$set": {
                "status": new_status,
                "punch_in": target_punch_in,
                "punch_out": target_punch_out,
                "work_hours": new_work_hours,
                "late_minutes": new_late_minutes,
                "overtime_minutes": new_overtime_minutes,
                "half_day": new_half_day,
            },
            "$push": {
                "history": history_entry
            }
        },
        return_document=ReturnDocument.AFTER
    )

    if not updated_record:
        raise HTTPException(status_code=409, detail="Concurrent regularize modification detected")

    return format_attendance_doc(updated_record)
