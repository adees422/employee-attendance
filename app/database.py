from pymongo import MongoClient
from app.config import MONGO_URI, MONGO_DB

# PyMongo Client (tz_aware=True per DATA_MODEL.md)
client: MongoClient = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]


def ensure_indexes() -> None:
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


def close_database() -> None:
    """Close MongoDB connection pool."""
    client.close()
