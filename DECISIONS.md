# DECISIONS.md

1. **Indexes.** We created a unique index on `employees.emp_code` and a compound index on `employees: (department, joined_on)` for department and headcount filtering. On `attendance_logs`, we created a unique compound index on `(emp_code, date)` to guarantee one punch per day; `(date: -1, emp_code: 1)` to serve paginated attendance queries without in-memory sorting; `(date, late_minutes, emp_code)` for fast leaderboard aggregation; and `(emp_code, punch_in: -1)` for punch-out resolution. We rejected an index on `status` alone due to low cardinality (only 5 distinct values).

2. **Punch-in race.** Both concurrent requests compute the attendance date and attempt `insert_one`. Because the collection enforces a unique compound index on `(emp_code, date)`, MongoDB's storage engine atomically commits exactly one insert (returning HTTP 201). The second request fails with a `DuplicateKeyError`, which our code intercepts to return HTTP 409 Conflict without race windows.

3. **Ties.** The leaderboard aggregation assigns standard competition rank using MongoDB's `$rank` window operator (e.g. 1, 2, 2, 4), and applies `rank <= limit` *after* ranking. When employees tie at the cutoff rank, all tied employees are returned, which can exceed `limit` rows as specified by the contract. Tied rows are sorted by `emp_code` ascending.

4. **Headcount.** The department summary aggregation begins on the `employees` collection filtered by `joined_on <= month_end`, and performs a correlated `$lookup` for logs. Employees with zero attendance logs still traverse the pipeline, contributing 1 to department headcount and 0 to log totals.

5. **100x Data Change.** We would maintain incremental pre-aggregated rollups using MongoDB Change Streams instead of on-demand cross-collection aggregations, and shard `attendance_logs` by `{emp_code: "hashed", date: 1}`.
