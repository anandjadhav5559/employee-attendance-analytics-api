# REVIEW.md

I reviewed the starter application against the API contract, business rules R1–R10, and the MongoDB data model.

#

Where (function / line)

What is wrong

How you'd notice it (test, input, or symptom)

How you fixed it

1

/health — health endpoint

The endpoint does not reliably verify that MongoDB is reachable before reporting readiness.

Stop MongoDB and call GET /health.

Ping MongoDB and return HTTP 503 when the database is unavailable.

2

create_employee — employee creation

Checking for duplicate employee codes before inserting is not sufficient under concurrent requests.

Send parallel requests with the same emp_code.

Enforce uniqueness with a MongoDB unique index and translate duplicate-key errors into HTTP 409.

3

list_employees — employee pagination

Pagination must validate page values, apply filters consistently, and return the correct filtered total and deterministic ordering.

Try page 0, page sizes above 100, and filtered requests across multiple pages.

Validate pagination parameters, count the filtered query, and sort by emp_code before applying skip and limit.

4

punch_in — attendance creation

A check followed by an insert can allow simultaneous requests to create duplicate attendance records.

Send concurrent punch-in requests for the same employee and attendance date.

Add a unique compound index on (emp_code, date) and handle duplicate-key errors as HTTP 409.

5

Punch-in and punch-out timestamp handling

Timestamp conversion must reject invalid epoch-millisecond inputs and truncate instants to whole seconds before calculations and storage.

Submit timestamps in seconds, strings, floats, or values containing fractional seconds.

Validate timestamp inputs, convert them to UTC datetimes, and truncate microseconds before calculations.

6

Attendance calculation helpers

Grace-period, overnight-shift, overtime-threshold, and rounding rules must be applied exactly as specified.

Test the 10-minute grace boundary, overnight shifts, 29 versus 30 overtime minutes, and half-up rounding boundaries.

Apply the documented IST shift rules, floor elapsed minutes, enforce the 30-minute overtime threshold, and use Decimal with ROUND_HALF_UP for work hours.

7

Attendance listing

Loading and sorting a large set of attendance records in Python before pagination does not scale well.

Test listing with a large dataset and inspect the query plan.

Apply filtering, sorting, skip, and limit in MongoDB and create indexes that support the query patterns.

Not considered defects

A weekday with no attendance record is not automatically an ABSENT record. The data model explicitly distinguishes missing records from recorded absences.

A half-day contributes 0.5 to present_days; it should not be counted as a full day.

Analytics should use stored derived values such as work_hours, late_minutes, and overtime_minutes, rather than recomputing them from punch timestamps.