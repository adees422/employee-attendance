from typing import Any, Dict, List, Optional
from app.services.time_utils import get_month_date_range


def build_department_summary_pipeline(month: str, department: Optional[str] = None) -> List[Dict[str, Any]]:
    m_start, m_end = get_month_date_range(month)
    month_start_str = m_start.isoformat()
    month_end_str = m_end.isoformat()

    match_stage: Dict[str, Any] = {"joined_on": {"$lte": month_end_str}}
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
