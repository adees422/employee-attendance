from datetime import datetime
from typing import Any, Dict
from app.services.time_utils import datetime_to_ms


def format_employee_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format an employee document for API response (no _id, instants to epoch ms)."""
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
