from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.database import db
from app.schemas.employee import EmployeeCreate
from app.services.formatters import format_employee_doc

router = APIRouter(tags=["employees"])


@router.post("/employees", status_code=status.HTTP_201_CREATED)
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


@router.get("/employees")
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
