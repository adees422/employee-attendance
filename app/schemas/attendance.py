from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import EpochMillis, StatusType, PresenceStatusType


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: PresenceStatusType = "PRESENT"


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeRequest(BaseModel):
    status: Optional[StatusType] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
