from typing import Annotated, Literal
from pydantic import Field

# Instant as epoch milliseconds since 1970-01-01T00:00:00Z
EpochMillis = Annotated[
    int,
    Field(
        strict=True,
        ge=100000000000,
        le=4102444800000,
        description="Instant as epoch milliseconds since 1970-01-01T00:00:00Z"
    )
]

StatusType = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatusType = Literal["PRESENT", "WFH", "ON_DUTY"]
