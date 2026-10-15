from datetime import datetime, timedelta
from typing import Any, Dict, List


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
