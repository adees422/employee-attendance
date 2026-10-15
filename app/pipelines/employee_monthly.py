from typing import Any, Dict, List
from app.services.time_utils import get_month_date_range


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
