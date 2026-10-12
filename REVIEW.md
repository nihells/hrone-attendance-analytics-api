# REVIEW.md — Review of the starter attendance API

This review records defects found in the starter implementation and the corresponding safeguards included or required in the revised implementation. Validate each behaviour against `openapi.yaml` and the problem statement before submission.

| Area | Risk in the starter | Correctness approach |
|---|---|---|
| Employee uniqueness | A pre-insert lookup is vulnerable to concurrent requests; both can pass it. | Keep a unique index on `emp_code`; translate duplicate-key conflicts into HTTP 409. |
| Attendance uniqueness | Concurrent punch-ins can create duplicate daily records without a database constraint. | Enforce a unique compound index on `(emp_code, date)` and handle `DuplicateKeyError` as HTTP 409. |
| Timestamps | Epoch milliseconds can be confused with seconds or returned as datetime strings. | Accept strict integer milliseconds, validate plausible bounds, truncate to whole seconds, store UTC BSON datetimes, and serialize API instants as epoch milliseconds. |
| Time-zone boundaries | The server's local timezone can differ from the attendance business timezone. | Derive attendance dates and shift calculations using IST, while treating stored BSON datetimes as UTC instants. |
| Shift rules | Overnight shifts can be assigned to the wrong calendar date; grace and overtime thresholds can be applied inclusively by mistake. | Associate early-morning punches with the prior overnight-shift date; apply the 10-minute late grace and 30-minute overtime minimum exactly as specified. |
| Punch-out race | Two requests can both read an open record before either writes. | Use a conditional update that only succeeds while `punch_out` is still `null`; return a conflict if another request closes it first. |
| Manual corrections | A correction can change derived values without recording what changed, or concurrent corrections can overwrite each other. | Recompute derived attendance fields, append an audit entry containing actual before/after changes, and use an optimistic concurrency predicate when updating. |
| Weekday attendance | Weekend attendance can incorrectly contribute to weekday-only presence statistics. | Restrict `present_days` calculations to Monday–Friday while retaining weekend records for metrics the specification says include weekends. |
| Headcount | Aggregating only attendance logs omits employees who have no logs. | Derive headcount from employees hired by the period end; do not infer headcount from attendance records. |
| Trend dates and average | Grouping only existing logs omits dates without records; doing date filling or the moving average in application code violates the aggregation requirement. | Build every date in the requested range in MongoDB, calculate daily metrics there, and use a seven-document window for the calendar-day moving average. |
| Leaderboard ties | Sorting and truncating before ranking can produce incorrect competition ranks at the cutoff. | Calculate `$rank` on total late minutes and include all rows whose competition rank is at most the requested limit. |
| Explain endpoint | A hand-written pipeline can differ from the endpoint it claims to explain. | Reuse the endpoint's filters and pipeline builder where possible; inspect `executionStats` and index stages against a realistic data set. |
| Index design | Missing indexes can cause avoidable collection scans; too many indexes increase write/storage cost. | Create indexes idempotently at startup and verify plans for the real endpoint queries, including employee department/join-date filters. |

## Verification notes

The revised Python file passes `py_compile`, and the `/health` endpoint has been reported to return HTTP 200 in the local environment. These checks do **not** constitute full integration or contract testing. Before submission, run the API against MongoDB 6.0+ and verify the complete OpenAPI contract, aggregation outputs, error cases, and explain plans. Record any unresolved limitations in `README.md` rather than claiming unverified behaviour.
