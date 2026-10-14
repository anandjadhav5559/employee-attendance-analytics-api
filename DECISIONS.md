# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. Indexes

I created a unique index on employees.emp_code to enforce employee identity, and a unique compound index on attendance_logs (emp_code, date) to prevent duplicate daily attendance records. Other indexes support department-filtered employee lists, attendance queries by employee and date, date-based analytics, and status-based queries. I would avoid adding indexes without a query that benefits from them because every index increases storage and write costs.

2. Punch-in race

Two simultaneous punch-in requests can both pass an initial existence check. The unique compound index on (emp_code, date) is the final safeguard. MongoDB accepts one insert; the other raises a duplicate-key error, which the API converts to HTTP 409. This avoids relying on a non-atomic check-then-insert operation.

3. Ties

The leaderboard should use a deterministic ranking rule for employees tied on late minutes. I would use a stable secondary sort such as emp_code so results are predictable. If the contract requires including all employees tied at the limit cutoff, the implementation must use tie-aware ranking and may return more than limit employees.

4. Headcount

The department summary starts from the employee collection and joins attendance data for the selected month. This means employees with no attendance records are still included in headcount. The join must also respect the reporting period and employees' joining dates.

5. Scaling to 100x the data

I would benchmark the aggregation pipelines against a representative large dataset using MongoDB explain. I would review examined documents, index use, lookup costs, and memory usage, then optimize indexes and pipeline stages based on those results. I would also add automated tests for business-rule boundaries and concurrent requests.