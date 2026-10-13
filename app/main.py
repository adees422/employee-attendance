"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

Complete implementation adhering strictly to:
- PROBLEM_STATEMENT.docx
- openapi.yaml (Contract v2.0.0, Business Rules R1-R10)
- DATA_MODEL.md (Extended JSON schemas, stored derived values)
"""
import os
import json
from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Any, Dict, List, Literal, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

# Load .env without overriding existing system environment variables
load_dotenv(override=False)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

# Timezone definition: IST is UTC+05:30
IST = timezone(timedelta(hours=5, minutes=30))

# PyMongo Client (Module-level, tz_aware=True per DATA_MODEL.md)
client: MongoClient = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]


# --------------------------------------------------------------------------- #
# Indexes and Lifespan
# --------------------------------------------------------------------------- #
def ensure_indexes():
    """Create all required indexes idempotently at startup."""
    # employees collection indexes
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("joined_on", 1)])

    # attendance_logs collection indexes
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", -1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure indexes idempotently
    try:
        ensure_indexes()
    except Exception as e:
        print(f"Warning: Failed to ensure indexes at startup: {e}")
    yield
    # Shutdown
    client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan
)


# --------------------------------------------------------------------------- #
# Custom Types and Constraints
# --------------------------------------------------------------------------- #
EpochMillis = Annotated[
    int,
    Field(
        strict=True,
        ge=100000000000,
        le=4102444800000,
        description="Instant as epoch milliseconds since 1970-01-01T00:00:00Z"
    )
]

StatusType = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatusType = Literal["PRESENT", "WFH", "ON_DUTY"]


# --------------------------------------------------------------------------- #
# Helper Functions - Date, Time, and Business Rules (R1 - R8)
# --------------------------------------------------------------------------- #
def round_half_up(val: float | Decimal, decimals: int = 2) -> float:
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


def compute_late_minutes(shift_start: str, attendance_date_str: str, punch_in_dt: datetime) -> int:
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


def compute_overtime(shift_start: str, shift_end: str, attendance_date_str: str, punch_out_dt: datetime) -> int:
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


def compute_work_hours(punch_in_dt: datetime, punch_out_dt: datetime) -> float:
    """
    R4: Work hours.
    (punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up.
    """
    sec = Decimal(str(int((punch_out_dt - punch_in_dt).total_seconds())))
    hours = sec / Decimal("3600")
    return round_half_up(hours, 2)


def is_half_day(work_hours: Optional[float]) -> bool:
    """
    R5: Half day.
    After punch-out, half_day is true when the rounded work_hours < 4.50.
    """
    if work_hours is None:
        return False
    return work_hours < 4.50


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


def format_employee_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format an employee document for API response."""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": datetime_to_ms(doc.get("created_at")),
    }


def format_attendance_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format an attendance document for API response (no _id/id, instants to epoch ms)."""
    raw_history = doc.get("history", [])
    history_formatted = []
    for h in raw_history:
        at_val = h.get("at")
        at_ms = datetime_to_ms(at_val) if isinstance(at_val, datetime) else at_val

        formatted_changes = {}
        for k, v in h.get("changes", {}).items():
            from_val = v.get("from")
            to_val = v.get("to")
            if k in ("punch_in", "punch_out"):
                formatted_changes[k] = {
                    "from": datetime_to_ms(from_val) if isinstance(from_val, datetime) else from_val,
                    "to": datetime_to_ms(to_val) if isinstance(to_val, datetime) else to_val,
                }
            else:
                formatted_changes[k] = {"from": from_val, "to": to_val}

        history_formatted.append({
            "at": at_ms,
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": formatted_changes,
        })

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": datetime_to_ms(doc.get("punch_in")),
        "punch_out": datetime_to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history_formatted,
    }


# --------------------------------------------------------------------------- #
# Pydantic Request / Response Models
# --------------------------------------------------------------------------- #
class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def validate_employee(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        try:
            date.fromisoformat(self.joined_on)
        except ValueError:
            raise ValueError("Invalid joined_on date")
        return self


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: PresenceStatusType = "PRESENT"


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeRequest(BaseModel):
    status: Optional[StatusType] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Pipelines Builders for Reusability and /admin/explain
# --------------------------------------------------------------------------- #
def build_employee_monthly_pipeline(emp_code: str, month: str) -> List[Dict[str, Any]]:
    m_start, m_end = get_month_date_range(month)
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": m_start.isoformat(), "$lte": m_end.isoformat()}
            }
        },
        {
            "$project": {
                "status": 1,
                "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                "half_day": {"$ifNull": ["$half_day", False]},
                "is_weekday": {
                    "$lte": [
                        {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                        5
                    ]
                },
                "is_present_status": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                "is_leave": {"$eq": ["$status", "LEAVE"]},
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$is_weekday", "$is_present_status"]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": ["$is_leave", 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": "$late_minutes"
                },
                "total_overtime_minutes": {
                    "$sum": "$overtime_minutes"
                }
            }
        }
    ]


def build_department_summary_pipeline(month: str, department: Optional[str] = None) -> List[Dict[str, Any]]:
    m_start, m_end = get_month_date_range(month)
    month_start_str = m_start.isoformat()
    month_end_str = m_end.isoformat()

    match_stage = {"joined_on": {"$lte": month_end_str}}
    if department:
        match_stage["department"] = department

    return [
        {"$match": match_stage},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp"]},
                                    {"$gte": ["$date", month_start_str]},
                                    {"$lte": ["$date", month_end_str]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$project": {
                "department": 1,
                "emp_code": 1,
                "logs": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {
                            "is_weekday": {
                                "$lte": [
                                    {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$$l.date"}}},
                                    5
                                ]
                            },
                            "is_present": {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            "is_leave": {"$eq": ["$$l.status", "LEAVE"]},
                            "is_on_duty": {"$eq": ["$$l.status", "ON_DUTY"]},
                            "half_day": {"$ifNull": ["$$l.half_day", False]},
                            "work_hours": "$$l.work_hours",
                            "late_minutes": {"$ifNull": ["$$l.late_minutes", 0]}
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "department": 1,
                "emp_code": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {
                                "$cond": [
                                    {"$and": ["$$l.is_weekday", "$$l.is_present"]},
                                    {"$cond": ["$$l.half_day", 0.5, 1.0]},
                                    0.0
                                ]
                            }
                        }
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": ["$$l.is_leave", 1, 0]}
                        }
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": ["$$l.is_on_duty", 1, 0]}
                        }
                    }
                },
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": [{"$gt": ["$$l.late_minutes", 0]}, 1, 0]}
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": "$logs.late_minutes"
                },
                "valid_work_hours": {
                    "$filter": {
                        "input": {
                            "$map": {
                                "input": "$logs",
                                "as": "l",
                                "in": {
                                    "$cond": [
                                        {"$and": ["$$l.is_present", {"$ne": ["$$l.work_hours", None]}]},
                                        "$$l.work_hours",
                                        None
                                    ]
                                }
                            }
                        },
                        "as": "wh",
                        "cond": {"$ne": ["$$wh", None]}
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "all_work_hours": {"$push": "$valid_work_hours"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": "$headcount",
                "present_days": "$present_days",
                "leave_count": "$leave_count",
                "on_duty_count": "$on_duty_count",
                "late_count": "$late_count",
                "total_late_minutes": "$total_late_minutes",
                "all_work_hours_flat": {
                    "$reduce": {
                        "input": "$all_work_hours",
                        "initialValue": [],
                        "in": {"$concatArrays": ["$$value", "$$this"]}
                    }
                }
            }
        },
        {
            "$project": {
                "department": 1,
                "headcount": 1,
                "present_days": 1,
                "leave_count": 1,
                "on_duty_count": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": [{"$size": "$all_work_hours_flat"}, 0]},
                        {"$avg": "$all_work_hours_flat"},
                        None
                    ]
                }
            }
        },
        {"$sort": {"department": 1}}
    ]


def build_late_leaderboard_pipeline(month: str, limit: int = 10, department: Optional[str] = None) -> List[Dict[str, Any]]:
    m_start, m_end = get_month_date_range(month)
    pipeline: List[Dict[str, Any]] = [
        {
            "$match": {
                "date": {"$gte": m_start.isoformat(), "$lte": m_end.isoformat()},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": "$rank",
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": "$total_late_minutes",
                "late_count": "$late_count"
            }
        }
    ])
    return pipeline


def build_department_trend_pipeline(department: str, from_date_str: str, to_date_str: str) -> List[Dict[str, Any]]:
    from_dt = datetime.fromisoformat(from_date_str)
    to_dt = datetime.fromisoformat(to_date_str)
    bounds_end = to_dt + timedelta(days=1)

    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "date_dt": from_dt
            }
        },
        {
            "$densify": {
                "field": "date_dt",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_dt, bounds_end]
                }
            }
        },
        {
            "$project": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date_dt"
                    }
                },
                "is_working_day": {
                    "$lte": [
                        {"$isoDayOfWeek": "$date_dt"},
                        5
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "pipeline": [
                    {"$match": {"department": department}},
                    {"$project": {"_id": 0, "emp_code": 1, "joined_on": 1}}
                ],
                "as": "dept_employees"
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$dept_employees",
                            "as": "e",
                            "cond": {"$lte": ["$$e.joined_on", "$date"]}
                        }
                    }
                },
                "dept_emp_codes": "$dept_employees.emp_code"
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date", "codes": "$dept_emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {"$in": ["$emp_code", "$$codes"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "day_logs"
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$day_logs",
                            "as": "l",
                            "in": {
                                "$cond": [
                                    {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$cond": [{"$ifNull": ["$$l.half_day", False]}, 0.5, 1.0]},
                                    0.0
                                ]
                            }
                        }
                    }
                },
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$day_logs",
                            "as": "l",
                            "in": {
                                "$cond": [{"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}, 1, 0]
                            }
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, "current"]
                        }
                    }
                }
            }
        },
        {"$sort": {"date": 1}}
    ]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

# 1. GET /health
@app.get("/health", tags=["system"])
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


# 2. POST /employees
@app.post("/employees", status_code=status.HTTP_201_CREATED, tags=["employees"])
def create_employee(body: EmployeeCreate):
    created_at_utc = datetime.now(timezone.utc).replace(microsecond=0)
    doc = {
        "emp_code": body.emp_code,
        "name": body.name,
        "email": body.email,
        "department": body.department,
        "shift_start": body.shift_start,
        "shift_end": body.shift_end,
        "joined_on": body.joined_on,
        "created_at": created_at_utc,
    }
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    return format_employee_doc(doc)


# 3. GET /employees
@app.get("/employees", tags=["employees"])
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    q: Dict[str, Any] = {}
    if department is not None:
        q["department"] = department

    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.employees.find(q).sort("emp_code", 1).skip(skip).limit(page_size)

    items = [format_employee_doc(d) for d in cursor]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


# 4. POST /attendance/punch-in
@app.post("/attendance/punch-in", status_code=status.HTTP_201_CREATED, tags=["attendance"])
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


# 5. POST /attendance/punch-out
@app.post("/attendance/punch-out", status_code=status.HTTP_200_OK, tags=["attendance"])
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


# 6. GET /attendance
@app.get("/attendance", tags=["attendance"])
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


# 7. PATCH /attendance/{emp_code}/{date}
@app.patch("/attendance/{emp_code}/{date}", tags=["attendance"])
def regularize_attendance(
    emp_code: str = Path(...),
    date_str: str = Path(..., alias="date", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    body: RegularizeRequest = ...
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
            target_punch_in = None if old_status in ("ABSENT", "LEAVE") else record.get("punch_in")

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
            target_punch_out = None if old_status in ("ABSENT", "LEAVE") else record.get("punch_out")

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


# 8. GET /analytics/employees/{emp_code}/monthly
@app.get("/analytics/employees/{emp_code}/monthly", tags=["analytics"])
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
        present_days = round_half_up(agg.get("present_days", 0.0), 2)
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


# 9. GET /analytics/departments/summary
@app.get("/analytics/departments/summary", tags=["analytics"])
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


# 10. GET /analytics/leaderboard/late
@app.get("/analytics/leaderboard/late", tags=["analytics"])
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


# 11. GET /analytics/departments/{department}/trend
@app.get("/analytics/departments/{department}/trend", tags=["analytics"])
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


# 12. GET /admin/explain/{endpoint}
@app.get("/admin/explain/{endpoint}", tags=["admin"])
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
