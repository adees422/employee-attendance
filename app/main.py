"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

Adhering strictly to:
- PROBLEM_STATEMENT.docx
- openapi.yaml (Contract v2.0.0, Business Rules R1-R10)
- DATA_MODEL.md (Extended JSON schemas, stored derived values)
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.database import client, db, ensure_indexes, close_database
from app.routers import system, employees, attendance, analytics, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure indexes idempotently
    try:
        ensure_indexes()
    except Exception as e:
        print(f"Warning: Failed to ensure indexes at startup: {e}")
    yield
    # Shutdown: cleanly close database connection
    close_database()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan
)

# Include modular API routers
app.include_router(system.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(analytics.router)
app.include_router(admin.router)

# Re-export key database objects for backward compatibility with test runners and scripts
__all__ = ["app", "db", "client", "ensure_indexes"]
