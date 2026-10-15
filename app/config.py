import os
from datetime import timedelta, timezone
from dotenv import load_dotenv

load_dotenv(override=False)

MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB: str = os.getenv("MONGO_DB", "attendance_db")

# Timezone definition: IST is UTC+05:30 (R1)
IST: timezone = timezone(timedelta(hours=5, minutes=30))
