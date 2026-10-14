"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    tzinfo=timezone.utc,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="ix_employees_department_emp_code",
    )

    db.employees.create_index(
        [("joined_on", 1), ("department", 1)],
        name="ix_employees_joined_department",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_date",
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="ix_attendance_date_emp",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", -1)],
        name="ix_attendance_emp_date_desc",
    )

    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)],
        name="ix_attendance_status_date_emp",
    )

    
@app.on_event("startup")
def startup_event():
    create_indexes()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def to_ist(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def truncate_seconds(value: datetime) -> datetime:
    return value.replace(microsecond=0)


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> int:
    local_time = to_ist(punch_in)
    shift_date = local_time.date()

    start_time = time.fromisoformat(shift_start)
    end_time = time.fromisoformat(shift_end)

    if end_time <= start_time and local_time.time() < end_time:
        shift_date -= timedelta(days=1)

    start = datetime.combine(
        shift_date,
        start_time,
        tzinfo=IST,
    )

    elapsed = (local_time - start).total_seconds()

    if elapsed <= 600:
        return 0

    return int(elapsed // 60)



def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )
    hours = seconds / Decimal("3600")

    return float(
        hours.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    start_time = time.fromisoformat(shift_start)
    end_time = time.fromisoformat(shift_end)

    shift_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    end = datetime.combine(
        shift_date,
        end_time,
        tzinfo=IST,
    )

    # If the shift crosses midnight, its end is on the next day.
    if end_time <= start_time:
        end += timedelta(days=1)

    elapsed_seconds = (to_ist(punch_out) - end).total_seconds()
    overtime_minutes = int(elapsed_seconds // 60)

    # Overtime is counted only when it reaches 30 minutes.
    return overtime_minutes if overtime_minutes >= 30 else 0




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
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None



class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str


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
            detail="MongoDB unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="emp_code already exists",
            )
        raise

    doc.pop("_id", None)
    return doc




@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
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

    query = {}

    if department:
        query["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    if body.punched_at is not None:
        try:
            ts = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        ts = datetime.now(timezone.utc)

    ts = truncate_seconds(ts)
    local_time = to_ist(ts)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    
    attendance_date = local_time.date()

    shift_start_time = time.fromisoformat(shift_start)
    shift_end_time = time.fromisoformat(shift_end)
    local_clock_time = local_time.time().replace(tzinfo=None)

    # For an overnight shift, early-morning punches
    # belong to the previous day's shift.
    if (
        shift_end_time <= shift_start_time
        and local_clock_time < shift_end_time
    ):
        attendance_date -= timedelta(days=1)


    date_str = attendance_date.isoformat()

    existing = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": date_str,
    })

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date",
        )

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            shift_start,
            shift_end,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="Already punched in for this date",
            )
        raise

    doc.pop("_id", None)
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            ts = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        ts = datetime.now(timezone.utc)

    ts = truncate_seconds(ts)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    punch_in_time = record["punch_in"]

    if ts <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if ts > punch_in_time + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    work_hours = compute_work_hours(punch_in_time, ts)

    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    half_day = work_hours < 4.50

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

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    return updated


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
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

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to",
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if status:
        if status not in {
            "PRESENT",
            "WFH",
            "ON_DUTY",
            "ABSENT",
            "LEAVE",
        }:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status",
            )
        query["status"] = status

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    skip = (page - 1) * page_size

    total = db.attendance_logs.count_documents(query)

    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([
            ("date", -1),
            ("emp_code", 1),
        ])
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    from datetime import time
    from pymongo.errors import DuplicateKeyError

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    if not 5 <= len(body.reason) <= 200:
        raise HTTPException(
            status_code=422,
            detail="reason must contain 5 to 200 characters",
        )

    if not 1 <= len(body.regularized_by) <= 50:
        raise HTTPException(
            status_code=422,
            detail="regularized_by must contain 1 to 50 characters",
        )

    changes_requested = body.model_fields_set.intersection(
        {"status", "punch_in", "punch_out"}
    )

    if not changes_requested:
        raise HTTPException(
            status_code=422,
            detail="At least one attendance field must be supplied",
        )

    emp = db.employees.find_one({"emp_code": emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    old_status = record["status"]
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    new_status = (
        body.status
        if "status" in body.model_fields_set
        else old_status
    )

    if new_status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    if new_status in {"ABSENT", "LEAVE"}:
        if (
            "punch_in" in body.model_fields_set
            or "punch_out" in body.model_fields_set
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )
        new_in = None
        new_out = None
    else:
        if "punch_in" in body.model_fields_set:
            if body.punch_in is None:
                raise HTTPException(
                    status_code=422,
                    detail="punch_in cannot be null for a presence status",
                )
            if (
                isinstance(body.punch_in, bool)
                or not isinstance(body.punch_in, int)
                or body.punch_in < 100000000000
            ):
                raise HTTPException(
                    status_code=422,
                    detail="punch_in must be epoch milliseconds",
                )
            try:
                new_in = datetime.fromtimestamp(
                    body.punch_in / 1000,
                    tz=timezone.utc,
                )
            except (ValueError, OverflowError, OSError):
                raise HTTPException(
                    status_code=422,
                    detail="Invalid punch_in timestamp",
                )
            new_in = truncate_seconds(new_in)
        else:
            new_in = old_in

        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="A presence status requires punch_in",
            )

        local_in = to_ist(new_in)
        expected_date = local_in.date()

        if (
            emp["shift_end"] <= emp["shift_start"]
            and local_in.time() < time.fromisoformat(emp["shift_end"])
        ):
            expected_date -= timedelta(days=1)

        if expected_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        if "punch_out" in body.model_fields_set:
            if body.punch_out is None:
                new_out = None
            else:
                if (
                    isinstance(body.punch_out, bool)
                    or not isinstance(body.punch_out, int)
                    or body.punch_out < 100000000000
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="punch_out must be epoch milliseconds",
                    )
                try:
                    new_out = datetime.fromtimestamp(
                        body.punch_out / 1000,
                        tz=timezone.utc,
                    )
                except (ValueError, OverflowError, OSError):
                    raise HTTPException(
                        status_code=422,
                        detail="Invalid punch_out timestamp",
                    )
                new_out = truncate_seconds(new_out)
        else:
            new_out = old_out

        if new_out is not None:
            if new_out <= new_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )
            if new_out > new_in + timedelta(hours=24):
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be within 24 hours of punch_in",
                )

    if new_status in {"ABSENT", "LEAVE"}:
        new_late = 0
        new_hours = None
        new_overtime = 0
        new_half_day = False
    else:
        new_late = compute_late_minutes(
            new_in,
            emp["shift_start"],
            emp["shift_end"],
        )

        if new_out is None:
            new_hours = None
            new_overtime = 0
            new_half_day = False
        else:
            new_hours = compute_work_hours(new_in, new_out)
            new_overtime = compute_overtime(
                new_out,
                emp["shift_start"],
                emp["shift_end"],
                date,
            )
            new_half_day = new_hours < 4.50

    final_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": new_hours,
        "late_minutes": new_late,
        "overtime_minutes": new_overtime,
        "half_day": new_half_day,
    }

    original_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    history_changes = {}

    for field, new_value in final_values.items():
        old_value = original_values[field]

        if field in {"punch_in", "punch_out"}:
            if old_value is not None:
                old_value = int(old_value.timestamp() * 1000)
            if new_value is not None:
                new_value = int(new_value.timestamp() * 1000)

        if old_value != new_value:
            history_changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not history_changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )

    history = record.get("history", [])
    now = datetime.now(timezone.utc)

    history_entry = {
        "at": now,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": {
            field: {
                "from": (
                    datetime.fromtimestamp(
                        value["from"] / 1000,
                        tz=timezone.utc,
                    )
                    if field in {"punch_in", "punch_out"}
                    and value["from"] is not None
                    else value["from"]
                ),
                "to": (
                    datetime.fromtimestamp(
                        value["to"] / 1000,
                        tz=timezone.utc,
                    )
                    if field in {"punch_in", "punch_out"}
                    and value["to"] is not None
                    else value["to"]
                ),
            }
            for field, value in history_changes.items()
        },
    }

    update_filter = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
    }

    if "history" in record:
        update_filter["history"] = history
    else:
        update_filter["history"] = {"$exists": False}

    try:
        result = db.attendance_logs.update_one(
            update_filter,
            {
                "$set": final_values,
                "$push": {"history": history_entry},
            },
        )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently",
        )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        if updated.get(field) is not None:
            updated[field] = int(updated[field].timestamp() * 1000)

    for entry in updated.get("history", []):
        entry["at"] = int(entry["at"].timestamp() * 1000)
        for field, change in entry["changes"].items():
            if field in {"punch_in", "punch_out"}:
                for side in ("from", "to"):
                    if change.get(side) is not None:
                        change[side] = int(
                            change[side].timestamp() * 1000
                        )

    return updated



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    import re
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    month_start = datetime.strptime(
        f"{month}-01",
        "%Y-%m-%d",
    ).date()

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    month_end = next_month - timedelta(days=1)

    joined_on = datetime.strptime(
        employee["joined_on"],
        "%Y-%m-%d",
    ).date()

    working_start = max(month_start, joined_on)

    working_days = 0
    current_day = working_start

    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$set": {
                "_attendanceDate": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
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
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$not": [
                                            {
                                                "$in": [
                                                    {
                                                        "$dayOfWeek": "$_attendanceDate"
                                                    },
                                                    [1, 7],
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    if results:
        totals = results[0]
    else:
        totals = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0,
        }

    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = float(
            (
                Decimal(str(totals["present_days"]))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": totals["present_days"],
        "leave_days": totals["leave_days"],
        "late_count": totals["late_count"],
        "total_late_minutes": totals["total_late_minutes"],
        "total_overtime_minutes": totals["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }



@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    import re
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    month_start = datetime.strptime(
        f"{month}-01",
        "%Y-%m-%d",
    ).date()

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    month_end = next_month - timedelta(days=1)

    employee_match = {
        "joined_on": {"$lte": month_end.isoformat()}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start.isoformat(),
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            month_end.isoformat(),
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "_attendanceDate": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                }
                            }
                        }
                    },
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$not": [
                                            {
                                                "$in": [
                                                    {
                                                        "$dayOfWeek": "$attendance._attendanceDate"
                                                    },
                                                    [1, 7],
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_total": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$attendance.work_hours", None]},
                                ]
                            },
                            "$attendance.work_hours",
                            0,
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
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$attendance.work_hours", None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$attendance.late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$attendance.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$attendance.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$attendance.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$headcount"},
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$work_hours_total",
                                        "$work_hours_count",
                                    ]
                                },
                                2,
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    for item in items:
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = float(
                Decimal(str(item["avg_work_hours"])).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

    return {
        "month": month,
        "items": items,
    }


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50",
        )

    month_start = datetime.strptime(
        f"{month}-01",
        "%Y-%m-%d",
    ).date()

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1,
        )

    month_end = next_month - timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {
                "$match": {
                    "employee.department": department,
                }
            }
        )

    pipeline.extend(
        [
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
            {
                "$setWindowFields": {
                    "partitionBy": (
                        "$department" if department is not None else None
                    ),
                    "sortBy": {"total_late_minutes": -1},
                    "output": {
                        "rank": {"$rank": {}},
                    },
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "emp_code": 1,
                }
            },
        ]
    )

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    import re
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", from_date):
        raise HTTPException(
            status_code=422,
            detail="'from' must use YYYY-MM-DD format",
        )

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date):
        raise HTTPException(
            status_code=422,
            detail="'to' must use YYYY-MM-DD format",
        )

    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid calendar date",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    day_count = (end_date - start_date).days + 1

    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if not db.employees.find_one({"department": department}, {"_id": 1}):
        raise HTTPException(
            status_code=404,
            detail="Unknown department",
        )

    pipeline = [
        {
            "$match": {
                "department": department,
                "joined_on": {"$lte": end_date.isoformat()},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", start_date.isoformat()]},
                                    {"$lte": ["$date", end_date.isoformat()]},
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {"$unwind": {"path": "$attendance", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$attendance.date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$attendance.status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$attendance.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$set": {
                "date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        


{
    "$densify": {
        "field": "date",
        "range": {
            "step": 1,
            "unit": "day",
            "bounds": [
                datetime.combine(start_date, datetime.min.time()),
                datetime.combine(
                    end_date + timedelta(days=1),
                    datetime.min.time(),
                ),
            ],
        },
    }
},


        {
            "$set": {
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                    }
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {"$lte": ["$joined_on", "$$trend_date"]},
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_data",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_data.count", 0]},
                        0,
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {
                            "$round": [
                                {"$divide": ["$present_count", "$headcount"]},
                                4,
                            ]
                        },
                        None,
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
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    for item in items:
        for field in ("attendance_rate", "moving_avg_7d"):
            value = item.get(field)
            if value is not None:
                item[field] = float(
                    Decimal(str(value)).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP,
                    )
                )

    return {
        "department": department,
        "items": items,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    from pymongo.errors import OperationFailure

    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail="Unsupported explain endpoint",
        )

    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination parameters",
        )

    if not 1 <= limit <= 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50",
        )

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code


        
    if endpoint == "employee_monthly":
        import re

        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required",
            )

        if not month or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        month_start = datetime.strptime(
            f"{month}-01", "%Y-%m-%d"
        ).date()

        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1,
                month=1,
            )
        else:
            next_month = month_start.replace(
                month=month_start.month + 1,
            )

        month_end = next_month - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                }
            },
            {
                "$set": {
                    "_attendanceDate": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                        }
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
                                        {
                                            "$in": [
                                                "$status",
                                                [
                                                    "PRESENT",
                                                    "WFH",
                                                    "ON_DUTY",
                                                ],
                                            ]
                                        },
                                        {
                                            "$not": [
                                                {
                                                    "$in": [
                                                        {
                                                            "$dayOfWeek":
                                                                "$_attendanceDate"
                                                        },
                                                        [1, 7],
                                                    ]
                                                }
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$late_minutes", 0]},
                                        0,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    },
                }
            },
        ]

        try:
            result = db.command(
                "explain",
                {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            )
        except OperationFailure as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Explain failed: {exc}",
            )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

        
    if endpoint == "department_summary":
        import re

        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        month_start = datetime.strptime(
            f"{month}-01", "%Y-%m-%d"
        ).date()

        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1, month=1
            )
        else:
            next_month = month_start.replace(
                month=month_start.month + 1
            )

        month_end = next_month - timedelta(days=1)

        employee_match = {
            "joined_on": {"$lte": month_end.isoformat()}
        }

        if department is not None:
            employee_match["department"] = department

        pipeline = [
            {"$match": employee_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$employee_code",
                                            ]
                                        },
                                        {
                                            "$gte": [
                                                "$date",
                                                month_start.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$date",
                                                month_end.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {
                            "$set": {
                                "_attendanceDate": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                    }
                                }
                            }
                        },
                    ],
                    "as": "attendance",
                }
            },
            {
                "$unwind": {
                    "path": "$attendance",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$addToSet": "$emp_code"},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$not": [
                                                {
                                                    "$in": [
                                                        {
                                                            "$dayOfWeek": "$attendance._attendanceDate"
                                                        },
                                                        [1, 7],
                                                    ]
                                                }
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$attendance.half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "work_hours_total": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$ne": ["$attendance.work_hours", None]},
                                    ]
                                },
                                "$attendance.work_hours",
                                0,
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
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$ne": ["$attendance.work_hours", None]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$attendance.late_minutes", 0]},
                                        0,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {
                            "$ifNull": ["$attendance.late_minutes", 0]
                        }
                    },
                    "leave_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$attendance.status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "on_duty_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$attendance.status", "ON_DUTY"]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": {"$size": "$headcount"},
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {
                                "$round": [
                                    {
                                        "$divide": [
                                            "$work_hours_total",
                                            "$work_hours_count",
                                        ]
                                    },
                                    2,
                                ]
                            },
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        try:
            result = db.command(
                "explain",
                {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            )
        except OperationFailure as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Could not explain department summary: {exc}",
            )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": result,
        }

        if status:
            if status not in {
                "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
            }:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid attendance status",
                )
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

        try:
            result = db.command(
                "explain",
                {
                    "find": "attendance_logs",
                    "filter": query,
                    "sort": {"date": -1, "emp_code": 1},
                    "skip": (page - 1) * page_size,
                    "limit": page_size,
                },
                verbosity="executionStats",
            )
        except OperationFailure as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Explain failed: {exc}",
            )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    
    if endpoint == "late_leaderboard":
        import re

        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        if limit < 1 or limit > 50:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 50",
            )

        month_start = datetime.strptime(
            f"{month}-01",
            "%Y-%m-%d",
        ).date()

        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1,
                month=1,
            )
        else:
            next_month = month_start.replace(
                month=month_start.month + 1,
            )

        month_end = next_month - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {
                "$match": {
                    "total_late_minutes": {"$gt": 0},
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append(
                {
                    "$match": {
                        "employee.department": department,
                    }
                }
            )

        pipeline.extend(
            [
                {
                    "$project": {
                        "_id": 0,
                        "emp_code": "$_id",
                        "name": "$employee.name",
                        "department": "$employee.department",
                        "total_late_minutes": 1,
                        "late_count": 1,
                    }
                },
                {
                    "$setWindowFields": {
                        "partitionBy": (
                            "$department" if department is not None else None
                        ),
                        "sortBy": {"total_late_minutes": -1},
                        "output": {
                            "rank": {"$rank": {}},
                        },
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "emp_code": 1,
                    }
                },
            ]
        )

        result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    
    if endpoint == "department_trend":
        import re

        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required",
            )

        if (
            not date_from
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from)
            or not date_to
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to)
        ):
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        try:
            start_date = datetime.strptime(
                date_from, "%Y-%m-%d"
            ).date()
            end_date = datetime.strptime(
                date_to, "%Y-%m-%d"
            ).date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid calendar date",
            )

        if end_date < start_date:
            raise HTTPException(
                status_code=422,
                detail="'to' must be on or after 'from'",
            )

        if (end_date - start_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        if not db.employees.find_one(
            {"department": department},
            {"_id": 1},
        ):
            raise HTTPException(
                status_code=404,
                detail="Unknown department",
            )

        pipeline = [
            {
                "$match": {
                    "department": department,
                    "joined_on": {"$lte": end_date.isoformat()},
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$employee_code",
                                            ]
                                        },
                                        {
                                            "$gte": [
                                                "$date",
                                                start_date.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$date",
                                                end_date.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "attendance",
                }
            },
            {
                "$unwind": {
                    "path": "$attendance",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$group": {
                    "_id": "$attendance.date",
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$attendance.status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$attendance.half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
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
                                                "$attendance.late_minutes",
                                                0,
                                            ]
                                        },
                                        0,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}},
            {
                "$set": {
                    "date": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                        }
                    }
                }
            },
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [
                            datetime.combine(start_date, datetime.min.time()),
                            datetime.combine(
                                end_date + timedelta(days=1),
                                datetime.min.time(),
                            ),
                        ],
                    },
                }
            },
            {
                "$set": {
                    "date_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date",
                        }
                    },
                    "present_count": {"$ifNull": ["$present_count", 0]},
                    "late_count": {"$ifNull": ["$late_count", 0]},
                    "is_working_day": {
                        "$not": [
                            {
                                "$in": [
                                    {"$dayOfWeek": "$date"},
                                    [1, 7],
                                ]
                            }
                        ]
                    },
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"trend_date": "$date_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "department": department,
                                "$expr": {
                                    "$lte": ["$joined_on", "$$trend_date"]
                                },
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_data",
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_data.count", 0]},
                            0,
                        ]
                    }
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {
                                "$round": [
                                    {"$divide": ["$present_count", "$headcount"]},
                                    4,
                                ]
                            },
                            None,
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
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        try:
            result = db.command(
                "explain",
                {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            )
        except OperationFailure as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Explain failed: {exc}",
            )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": result,
        }

    raise HTTPException(
        status_code=501,
        detail=(
            "Explain support for this analytics endpoint "
            "has not been implemented yet"
        ),
    )


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
