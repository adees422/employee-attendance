from datetime import date
from pydantic import BaseModel, Field, model_validator


class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def validate_employee(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        try:
            date.fromisoformat(self.joined_on)
        except ValueError:
            raise ValueError("Invalid joined_on date")
        return self
