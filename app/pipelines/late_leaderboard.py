from typing import Any, Dict, List, Optional
from app.services.time_utils import get_month_date_range


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
