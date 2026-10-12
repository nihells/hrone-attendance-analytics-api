"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from calendar import monthrange
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt, Field
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="unique_employee_code",
    )

    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="employees_department_joined_on",
    )

    db.employees.create_index([("joined_on", 1)], name="employees_joined_on")

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="unique_employee_attendance_date",
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="attendance_date_employee",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("punch_in", -1)],
        name="employee_punch_in",
    )

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    attendance_date = datetime.fromisoformat(date_str).date()

    end_date = attendance_date

    # Overnight shift: the shift ends on the following day.
    if (end_h, end_m) <= (start_h, start_m):
        end_date += timedelta(days=1)

    shift_end_ist = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        end_h,
        end_m,
        tzinfo=IST,
    )

    # MongoDB stores BSON datetimes as UTC instants.
    punch_out_utc = punch_out.replace(tzinfo=timezone.utc)
    punch_out_ist = punch_out_utc.astimezone(IST)

    elapsed_minutes = int(
        (punch_out_ist - shift_end_ist).total_seconds() // 60
    )

    # Overtime counts only when it reaches 30 minutes.
    return elapsed_minutes if elapsed_minutes >= 30 else 0

def datetime_to_epoch_ms(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")

    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return float(rounded_hours)

def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: str,
) -> int:
    punch_in_ist = punch_in.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    h, m = map(int, shift_start.split(":"))

    shift_date = datetime.fromisoformat(attendance_date).date()

    shift_start_ist = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        h,
        m,
        tzinfo=IST,
    )

    elapsed_seconds = (
        punch_in_ist - shift_start_ist
    ).total_seconds()

    if elapsed_seconds <= 600:
        return 0

    return int(elapsed_seconds // 60)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"
class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
class AttendanceCorrection(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable"
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    import re

    if not re.fullmatch(r"EMP\d{4,6}", body.emp_code):
        raise HTTPException(422, "emp_code must match EMP followed by 4 to 6 digits")
    import re
    for field_name in ("name", "email", "department"):
        value = getattr(body, field_name)
        if not value.strip():
            raise HTTPException(422, f"{field_name} cannot be empty")
    if len(body.name) > 100 or len(body.email) > 120 or len(body.department) > 50:
        raise HTTPException(422, "name, email, or department exceeds the allowed length")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(422, "email must be a valid email address")
    try:
        joined = datetime.strptime(body.joined_on, "%Y-%m-%d")
        if joined.strftime("%Y-%m-%d") != body.joined_on:
            raise ValueError
        for value in (body.shift_start, body.shift_end):
            parsed_time = datetime.strptime(value, "%H:%M")
            if parsed_time.strftime("%H:%M") != value:
                raise ValueError
        if body.shift_start == body.shift_end:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "joined_on must be YYYY-MM-DD and shift times must be HH:MM with different start/end")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = datetime_to_epoch_ms(doc.get("created_at"))
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    q = {}

    if department:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = datetime_to_epoch_ms(item["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY"}

    if body.status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    # Validate epoch milliseconds and truncate to whole seconds.
    if body.punched_at is not None:
        if (
            type(body.punched_at) is not int
            or not 100000000000 <= body.punched_at <= 4102444800000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be a valid epoch-millisecond integer",
            )

        ts = datetime.fromtimestamp(
            body.punched_at // 1000,
            tz=timezone.utc,
        ).replace(tzinfo=None)

    else:
        ts = datetime.now(timezone.utc).replace(
            microsecond=0,
            tzinfo=None,
        )

    # Determine the attendance date using Indian Standard Time.
    punch_in_ist = ts.replace(tzinfo=timezone.utc).astimezone(IST)

    start_h, start_m = map(int, emp["shift_start"].split(":"))
    end_h, end_m = map(int, emp["shift_end"].split(":"))

    attendance_date = punch_in_ist.date()

    # For an overnight shift, an early-morning punch-in belongs
    # to the previous attendance date.
    if (
        (end_h, end_m) <= (start_h, start_m)
        and (punch_in_ist.hour, punch_in_ist.minute) < (end_h, end_m)
    ):
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
           ts,
           emp["shift_start"],
           date_str,
),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except Exception as exc:
        # A duplicate-key error is expected if a unique index
        # prevents multiple records for the same employee/date.
        

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="already punched in for this date",
            )

        raise

    doc.pop("_id", None)

    # Return timestamps in epoch milliseconds.
    doc["punch_in"] = int(
        ts.replace(tzinfo=timezone.utc).timestamp() * 1000
    )

    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate pagination.
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    # Validate date formats.
    for date_value in (date_from, date_to):
        if date_value is not None:
            try:
                parsed_date = datetime.strptime(
                    date_value, "%Y-%m-%d"
                )
                if parsed_date.strftime("%Y-%m-%d") != date_value:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format",
                )

    # Validate date range.
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be after date_to",
        )

    # Validate status values against the API contract.
    if status is not None and status not in {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    # Build MongoDB filters.
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}

        if date_from:
            q["date"]["$gte"] = date_from

        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    # Count only records matching the filters.
    total = db.attendance_logs.count_documents(q)

    # Pagination and sorting happen inside MongoDB.
    docs = list(
        db.attendance_logs.find(q)
        .sort(
            [
                ("date", -1),
                ("emp_code", 1),
            ]
        )
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = []

    for doc in docs:
        doc.pop("_id", None)

        # Return timestamps as epoch milliseconds.
                # Return timestamps as epoch milliseconds.
        for field in ("punch_in", "punch_out"):
            doc[field] = datetime_to_epoch_ms(doc.get(field))

        # Convert history timestamps if history exists.
        for entry in doc.get("history", []):
            value = entry.get("at")

            if isinstance(value, datetime):
                entry["at"] = datetime_to_epoch_ms(value)
            for change in entry.get("changes", {}).values():
                for key in ("from", "to"):
                    if isinstance(change.get(key), datetime):
                        change[key] = datetime_to_epoch_ms(change[key])

        items.append(doc)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    

    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Validate epoch milliseconds and truncate to whole seconds.
    if body.punched_at is not None:
        if (
            type(body.punched_at) is not int
            or not 100000000000 <= body.punched_at <= 4102444800000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be a valid epoch-millisecond integer",
            )

        ts = datetime.fromtimestamp(
            body.punched_at // 1000,
            tz=timezone.utc,
        ).replace(tzinfo=None)
    else:
        ts = datetime.now(timezone.utc).replace(
            microsecond=0,
            tzinfo=None,
        )

    # Find the most recent punch-in not later than punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found for this employee",
        )

    punch_in_time = record["punch_in"].replace(microsecond=0)

    if ts <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if (ts - punch_in_time).total_seconds() > 86400:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    # Calculate work hours and overtime.
    work_hours = compute_work_hours(punch_in_time, ts)

    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    half_day = work_hours < 4.50

    # Only one concurrent request can successfully close this record.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    updated.pop("_id", None)

    # Convert timestamps to epoch milliseconds for the API response.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None:
            updated[field] = int(
                value.replace(tzinfo=timezone.utc).timestamp() * 1000
            )

    return updated

@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrection,
):
    # Validate the date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
        if parsed_date.strftime("%Y-%m-%d") != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    # Check that the employee exists.
    emp = db.employees.find_one({"emp_code": emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Find the attendance record.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    request_data = body.model_dump(exclude_unset=True)
    reason = request_data.pop("reason")
    regularized_by = request_data.pop("regularized_by")
    changes = request_data

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one field to correct",
        )

    allowed_statuses = {
        "PRESENT",
        "WFH",
        "ON_DUTY",
        "ABSENT",
        "LEAVE",
    }

    if "status" in changes and changes["status"] not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # Validate supplied epoch-millisecond timestamps.
    for field in ("punch_in", "punch_out"):
        if field in changes and changes[field] is not None:
            value = changes[field]

            if (
                type(value) is not int
                or not 100000000000 <= value <= 4102444800000
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must be valid epoch milliseconds",
                )

            changes[field] = datetime.fromtimestamp(
                value // 1000,
                tz=timezone.utc,
            ).replace(tzinfo=None)

    # Construct the proposed attendance record.
    updated_record = {
        key: value
        for key, value in record.items()
        if key != "_id"
    }

    updated_record.update(changes)

    status = updated_record.get("status", "PRESENT")
    punch_in = updated_record.get("punch_in")
    punch_out = updated_record.get("punch_out")

    # ABSENT and LEAVE records cannot have punch timestamps.
    if status in {"ABSENT", "LEAVE"}:
        if "punch_in" in changes or "punch_out" in changes:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch timestamps",
            )

        punch_in = None
        punch_out = None

    elif punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence statuses require punch_in",
        )

    # Check punch-in belongs to the attendance date in IST.
    if punch_in is not None:
        punch_in_ist = punch_in.replace(
            tzinfo=timezone.utc
        ).astimezone(IST)

        if punch_in_ist.date().isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date in IST",
            )

    # Validate punch-out.
    if punch_out is not None:
        if punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )

        elapsed = (punch_out - punch_in).total_seconds()

        if elapsed <= 0 or elapsed > 86400:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    # Compute the proposed derived fields.
    if punch_in is not None:
        late_minutes = compute_late_minutes(
            punch_in,
            emp["shift_start"],
            date,
        )
    else:
        late_minutes = 0

    if punch_in is not None and punch_out is not None:
        work_hours = compute_work_hours(punch_in, punch_out)

        overtime_minutes = compute_overtime(
            punch_out,
            emp["shift_start"],
            emp["shift_end"],
            date,
        )

        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    # Determine actual field changes.
    set_fields = {
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    actual_changes = {}

    for field, new_value in set_fields.items():
        old_value = record.get(field)

        if old_value != new_value:
            actual_changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not actual_changes:
        raise HTTPException(
            status_code=422,
            detail="No actual changes supplied",
        )

    # Append an audit history entry.
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None),
        "by": regularized_by,
        "reason": reason,
        "changes": actual_changes,
    }

    set_values = {
        field: set_fields[field]
        for field in actual_changes
    }

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "history": record.get("history", [])},
        {
            "$set": set_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    updated.pop("_id", None)

    # Serialize timestamps as epoch milliseconds.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None:
            updated[field] = int(
                value.replace(
                    tzinfo=timezone.utc
                ).timestamp() * 1000
            )

    for entry in updated.get("history", []):
        value = entry.get("at")

        if isinstance(value, datetime):
            entry["at"] = int(
                value.replace(
                    tzinfo=timezone.utc
                ).timestamp() * 1000
            )
        for change in entry.get("changes", {}).values():
            for key in ("from", "to"):
                if isinstance(change.get(key), datetime):
                    change[key] = datetime_to_epoch_ms(change[key])

    return updated

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(emp_code: str, month: str):
    # Validate the month format
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")

        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    # Check whether the employee exists
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1}
    )

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # Calculate month boundaries
    year = parsed_month.year
    month_number = parsed_month.month

    month_start = f"{year:04d}-{month_number:02d}-01"
    month_end = (
        f"{year:04d}-{month_number:02d}-"
        f"{monthrange(year, month_number)[1]:02d}"
    )

    # MongoDB aggregation
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start,
                    "$lte": month_end
                }
            }
        },
        {
            "$group": {
                "_id": None,

                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0
                        ]
                    }
                },

                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },

                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },

                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },

                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        }
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    if results:
        result = results[0]
    else:
        result = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0
        }

    # Count Monday-Friday working days
    working_days = sum(
        1
        for day in range(1, monthrange(year, month_number)[1] + 1)
        if datetime(year, month_number, day).weekday() < 5
        and f"{year:04d}-{month_number:02d}-{day:02d}"
        >= employee.get("joined_on", month_start)
    )

    # Weekday presence is calculated in MongoDB in the aggregation above.
    present_days = float(Decimal(str(result.get("present_days", 0))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ))

    attendance_pct = (
        float((Decimal(str(present_days)) / Decimal(working_days) * Decimal(100))
              .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": result["leave_days"],
        "late_count": result["late_count"],
        "total_late_minutes": result["total_late_minutes"],
        "total_overtime_minutes": result["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None
):
    # Validate month format
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")

        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    # Calculate month boundaries
    year = parsed_month.year
    month_number = parsed_month.month

    month_start = f"{year:04d}-{month_number:02d}-01"

    from calendar import monthrange

    last_day = monthrange(year, month_number)[1]

    month_end = (
        f"{year:04d}-{month_number:02d}-{last_day:02d}"
    )

    # Get departments with employees who were hired
    # on or before the end of the requested month.
    employee_match = {
        "joined_on": {"$lte": month_end}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1}
            }
        },
        {
            "$match": {
                "headcount": {"$gt": 0}
            }
        },
        {
            "$sort": {
                "_id": 1
            }
        }
    ]

    departments = list(
        db.employees.aggregate(pipeline)
    )

    # Calculate attendance statistics for each department.
    output = []

    for dept in departments:
        dept_name = dept["_id"]

        attendance_pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start,
                        "$lte": month_end
                    },
                    "status": {
                        "$in": [
                            "PRESENT",
                            "WFH",
                            "ON_DUTY",
                            "ABSENT",
                            "LEAVE"
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"},
            {
                "$match": {
                    "employee.department": dept_name,
                    "employee.joined_on": {
                        "$lte": month_end
                    }
                }
            },
            {
                "$group": {
                    "_id": None,

                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
                                    ]
                                },
                                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                                0
                            ]
                        }
                    },

                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {
                                            "$ifNull": [
                                                "$late_minutes",
                                                0
                                            ]
                                        },
                                        0
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },

                    "total_late_minutes": {
                        "$sum": {
                            "$ifNull": [
                                "$late_minutes",
                                0
                            ]
                        }
                    },

                    "leave_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        "$status",
                                        "LEAVE"
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },

                    "on_duty_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        "$status",
                                        "ON_DUTY"
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },

                    "work_hours_sum": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$status",
                                                [
                                                    "PRESENT",
                                                    "WFH",
                                                    "ON_DUTY"
                                                ]
                                            ]
                                        },
                                        {
                                            "$ne": [
                                                "$work_hours",
                                                None
                                            ]
                                        }
                                    ]
                                },
                                "$work_hours",
                                0
                            ]
                        }
                    },

                    "work_hours_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$status",
                                                [
                                                    "PRESENT",
                                                    "WFH",
                                                    "ON_DUTY"
                                                ]
                                            ]
                                        },
                                        {
                                            "$ne": [
                                                "$work_hours",
                                                None
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    }
                }
            }
        ]

        attendance_result = list(
            db.attendance_logs.aggregate(
                attendance_pipeline
            )
        )

        if attendance_result:
            stats = attendance_result[0]
        else:
            stats = {
                "present_days": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0,
                "work_hours_sum": 0,
                "work_hours_count": 0
            }

        work_hours_count = stats["work_hours_count"]

        avg_work_hours = (
            float(Decimal(str(stats["work_hours_sum"] / work_hours_count)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ))
            if work_hours_count > 0
            else None
        )

        output.append({
            "department": dept_name,
            "headcount": dept["headcount"],
            "present_days": stats["present_days"],
            "avg_work_hours": avg_work_hours,
            "late_count": stats["late_count"],
            "total_late_minutes": stats["total_late_minutes"],
            "leave_count": stats["leave_count"],
            "on_duty_count": stats["on_duty_count"]
        })

    return {"month": month, "items": output}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    # Validate month
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")
        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    # Validate limit
    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50"
        )

    # Build aggregation pipeline
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": f"{month}-01",
                    "$lte": (
                        f"{month}-{monthrange(parsed_month.year, parsed_month.month)[1]:02d}"
                    )
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {
            "$match": (
                {"employee.department": department}
                if department is not None
                else {}
            )
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
                "rank": 1
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1
            }
        }
    ]

    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}

def _department_trend_pipeline(department: str, from_date: str, to_date: str, day_count: int) -> list:
    """Build the database-side trend pipeline shared by the endpoint and Explain."""
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "employees": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}}}},
        {"$project": {
            "_id": 0,
            "employees": 1,
            "days": {"$map": {
                "input": {"$range": [0, day_count]},
                "as": "offset",
                "in": {"$dateToString": {"format": "%Y-%m-%d", "date": {"$dateAdd": {
                    "startDate": {"$dateFromString": {"dateString": from_date}},
                    "unit": "day", "amount": "$$offset"
                }}}}
            }}
        }},
        {"$unwind": "$days"},
        {"$set": {
            "date": "$days",
            "headcount": {"$size": {"$filter": {
                "input": "$employees", "as": "employee",
                "cond": {"$lte": ["$$employee.joined_on", "$days"]}
            }}}
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"day": "$date", "codes": "$employees.emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$date", "$$day"]},
                    {"$in": ["$emp_code", "$$codes"]}
                ]}}},
                {"$group": {
                    "_id": None,
                    "present_count": {"$sum": {"$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$lte": ["$date", to_date]}
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0
                    ]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}
                }}
            ],
            "as": "daily"
        }},
        {"$set": {
            "present_count": {"$ifNull": [{"$arrayElemAt": ["$daily.present_count", 0]}, 0]},
            "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily.late_count", 0]}, 0]},
            "is_working_day": {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
            None
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]}
            }}
        }},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1,
            "moving_avg_7d": {"$cond": [
                {"$eq": [{"$type": "$moving_avg_7d"}, "missing"]}, None,
                {"$round": ["$moving_avg_7d", 4]}
            ]}
        }},
        {"$sort": {"date": 1}}
    ]


@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
        if start.isoformat() != from_date or end.isoformat() != to_date:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")
    if end < start:
        raise HTTPException(status_code=422, detail="to must not be before from")
    day_count = (end - start).days + 1
    if day_count > 92:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Department not found")
    pipeline = _department_trend_pipeline(department, from_date, to_date, day_count)
    return {"department": department, "items": list(db.employees.aggregate(pipeline, allowDiskUse=True))}


@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: str,
    month: Optional[str] = None,
    department: Optional[str] = None,
    emp_code: Optional[str] = None,
    limit: int = 10,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
):
    import calendar
    from bson import ObjectId
    from bson.timestamp import Timestamp
    from datetime import date

    allowed = {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}
    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported endpoint")

    def validate_month(value):
        try:
            parsed = datetime.strptime(value, "%Y-%m")
            if parsed.strftime("%Y-%m") != value: raise ValueError
            return parsed
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="month must use YYYY-MM format")

    def validate_date(value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
            if parsed.strftime("%Y-%m-%d") != value: raise ValueError
            return parsed.date()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")

    def safe(value):
        if isinstance(value, dict): return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)): return [safe(v) for v in value]
        if isinstance(value, (datetime, date)): return value.isoformat()
        if isinstance(value, ObjectId): return str(value)
        if isinstance(value, Timestamp): return {"time": value.time, "increment": value.inc}
        if isinstance(value, bytes): return value.hex()
        return value

    def run_explain(collection, pipeline):
        try:
            result = db.command("explain", {"aggregate": collection, "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
            return {"endpoint": endpoint, "collection": collection, "explain": safe(result)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"MongoDB explain failed: {exc}")

    if endpoint == "attendance_list":
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(status_code=422, detail="page must be >= 1 and page_size must be between 1 and 100")
        query = {}
        if emp_code: query["emp_code"] = emp_code
        if status: query["status"] = status
        start_val, end_val = date_from, date_to
        if month:
            parsed = validate_month(month)
            month_start = f"{month}-01"
            month_end = f"{month}-{calendar.monthrange(parsed.year, parsed.month)[1]:02d}"
            start_val = max(filter(None, [start_val, month_start]), default=month_start)
            end_val = min(filter(None, [end_val, month_end]), default=month_end)
        if start_val: validate_date(start_val)
        if end_val: validate_date(end_val)
        if start_val and end_val and start_val > end_val:
            raise HTTPException(status_code=422, detail="date_from cannot be after date_to")
        if start_val or end_val:
            query["date"] = {}
            if start_val: query["date"]["$gte"] = start_val
            if end_val: query["date"]["$lte"] = end_val
        try:
            command = {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            }
            result = db.command("explain", command, verbosity="executionStats")
            return {"endpoint": endpoint, "collection": "attendance_logs", "explain": safe(result)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"MongoDB explain failed: {exc}")

    if endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(status_code=422, detail="emp_code and month are required")
        parsed = validate_month(month)
        last_day = calendar.monthrange(parsed.year, parsed.month)[1]
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": f"{month}-01", "$lte": f"{month}-{last_day:02d}"}}},
            {"$group": {"_id": None,
                "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2,3,4,5,6]]}]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
            }}
        ]
        return run_explain("attendance_logs", pipeline)

    if endpoint == "department_summary":
        if not month: raise HTTPException(status_code=422, detail="month is required")
        parsed = validate_month(month)
        last_day = calendar.monthrange(parsed.year, parsed.month)[1]
        match = {"joined_on": {"$lte": f"{month}-{last_day:02d}"}}
        if department: match["department"] = department
        # Explain the employee/headcount aggregation used by the endpoint; no unwind,
        # so employees without attendance records remain part of headcount.
        return run_explain("employees", [{"$match": match}, {"$group": {"_id": "$department", "headcount": {"$sum": 1}}}, {"$sort": {"_id": 1}}])

    if endpoint == "late_leaderboard":
        if not month: raise HTTPException(status_code=422, detail="month is required")
        if not 1 <= limit <= 50: raise HTTPException(status_code=422, detail="limit must be between 1 and 50")
        parsed = validate_month(month)
        last_day = calendar.monthrange(parsed.year, parsed.month)[1]
        pipeline = [
            {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-{last_day:02d}"}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
            {"$unwind": "$employee"},
        ]
        if department: pipeline.append({"$match": {"employee.department": department}})
        pipeline += [
            {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
        ]
        return run_explain("attendance_logs", pipeline)

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(status_code=422, detail="department, from and to are required")
        start = validate_date(from_date); end = validate_date(to_date)
        if start > end: raise HTTPException(status_code=422, detail="'to' cannot be before 'from'")
        days = (end - start).days + 1
        if days > 92: raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")
        return run_explain("employees", _department_trend_pipeline(department, from_date, to_date, days))
