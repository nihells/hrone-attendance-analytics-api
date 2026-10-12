# DECISIONS.md

1. **Indexes.** A unique index on `employees.emp_code` prevents duplicate employee identifiers, and a unique compound index on `attendance_logs.emp_code` plus `date` enforces one attendance record per employee per attendance date. The date/employee index supports attendance-list sorting, while the employee/punch-in index supports finding an open record for punch-out. Department and join-date indexes support headcount and trend filtering. Indexes should be checked with `explain('executionStats')`; unnecessary indexes increase storage and write cost.

2. **Punch-in race.** A pre-check improves the error message but cannot guarantee uniqueness under concurrency. The database unique index is authoritative. A duplicate-key error becomes HTTP 409, so simultaneous requests cannot create two records for the same employee/date.

3. **Leaderboard ties.** Competition ranking is computed with MongoDB `$rank` over total late minutes. Filtering by `rank <= limit` keeps every employee tied at the cutoff rank; employee code is used only as a deterministic display order, not as part of the rank.

4. **Headcount without logs.** Headcount comes from employees whose `joined_on` is on or before the period end, not from attendance logs. Therefore employees with no attendance records remain in the denominator.

5. **Scaling.** Keep filtering, grouping, daily date generation, and the seven-calendar-day moving average in MongoDB aggregation rather than transferring raw records to Python. Use selective indexed matches, inspect explain plans on a representative 100k-record dataset, and measure before adding indexes. A larger deployment may require workload-specific indexes, connection-pool tuning, and pagination/aggregation profiling.
