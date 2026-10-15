from fastapi import APIRouter, HTTPException
from app.database import db

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
