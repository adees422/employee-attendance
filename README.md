# Employee Attendance & Analytics API

FastAPI and MongoDB backend for employee attendance tracking, manual corrections with audit trails, and MongoDB aggregation pipelines.

## Prerequisites
- Python 3.10+ (tested on Python 3.10 and 3.11)
- MongoDB 6.0+ (Atlas or local instance)

## Installation & Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables (optional, defaults provided):
```bash
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"
```

3. Seed initial sample data:
```bash
python sample_seed.py
```

4. Run the API:
```bash
uvicorn app.main:app --port 8000
```

Interactive API documentation will be available at `http://localhost:8000/docs`.

## Key Highlights
- **Indexes at Startup:** Created automatically and idempotently during application startup lifespan.
- **Race Condition Safety:** Database unique indexes on `(emp_code)` and `(emp_code, date)` eliminate check-then-insert race conditions on concurrent requests.
- **Business Rules Enforced:** Correct IST calendar calculations, overnight shifts, 10-minute grace period with floor minute calculation, >=30 minute overtime threshold, and half-up rounding.
- **Aggregation Pipelines:** Standard competition ranking with ties, cross-collection department summaries with non-log headcounts, and calendar gap-filling with `$densify` and 7-day moving averages.
- **Explain Endpoint:** `GET /admin/explain/{endpoint}` exposes query execution plans verifying `IXSCAN` index usage.

## Project Architecture & Modular Structure
```
app/
├── main.py                  # FastAPI app lifespan and router registration
├── config.py                # Environment configuration (MONGO_URI, MONGO_DB, IST timezone)
├── database.py              # MongoDB client, connection pool, and index creation
├── schemas/                 # Pydantic schemas and type validators
│   ├── common.py            # EpochMillis, StatusType, PresenceStatusType
│   ├── employee.py          # EmployeeCreate schema and validation
│   └── attendance.py        # PunchIn, PunchOut, Regularize schemas
├── services/                # Business logic, time calculations, formatters
│   ├── time_utils.py        # IST date/time parsing, epoch millis, working day counters
│   ├── business_rules.py    # Rules R1-R5 (shift cutoffs, grace periods, OT, work hours)
│   └── formatters.py        # Document serializers (instant conversions, omitting _id)
├── pipelines/               # MongoDB aggregation pipeline builders
│   ├── employee_monthly.py  # Monthly attendance aggregations
│   ├── department_summary.py# Cross-collection department metrics
│   ├── late_leaderboard.py  # Competition ranking ($rank) with ties
│   └── department_trend.py  # Calendar gap-filling ($densify) and 7-day moving averages
└── routers/                 # Modular API endpoints
    ├── system.py            # /health
    ├── employees.py         # /employees CRUD
    ├── attendance.py        # /attendance punch & regularization
    ├── analytics.py         # /analytics reports and trends
    └── admin.py             # /admin/explain query execution plans
```

## Submission Contents
- `app/`: Fully modular FastAPI application.
- `requirements.txt`: Python package dependencies.
- `REVIEW.md`: Comprehensive review of starter code defects.
- `DECISIONS.md`: Concise explanation of architectural and design decisions.
- `README.md`: Setup and execution guide.

