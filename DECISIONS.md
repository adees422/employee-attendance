# DECISIONS.md

1. **Indexes:**
I added a unique index on `employees.emp_code` and a compound index on `employees: (department, joined_on)` so department-level queries and headcount filtering hit indexes directly. For `attendance_logs`, I set up a unique compound index on `(emp_code, date)` to enforce one punch per employee per day at the database layer. I also added `(date: -1, emp_code: 1)` to handle paginated queries without in-memory sorting, `(date, late_minutes, emp_code)` for the leaderboard pipeline, and `(emp_code, punch_in: -1)` for open punch-out lookups. I avoided indexing `status` by itself because with only 5 possible values, the cardinality is too low to be useful.

2. **Punch-in race:**
When two concurrent punch-in requests arrive at the same time, both compute the IST shift date and attempt `insert_one`. Because MongoDB enforces the unique compound index on `(emp_code, date)`, the storage engine atomically commits one insert (which returns HTTP 201) while the second one immediately trips a `DuplicateKeyError`. The application catches this exception and returns an HTTP 409 Conflict. This avoids any check-then-insert race window.

3. **Ties:**
For the late leaderboard, I used MongoDB's native `$rank` window operator (`$setWindowFields`) which handles standard competition ranking (1, 2, 2, 4). The `limit` filter is applied *after* ranking (`rank <= limit`), ensuring that if multiple employees tie at the cutoff rank, all of them are returned even if the count exceeds `limit`. Any tied rows are deterministically sorted by `emp_code` ascending.

4. **Headcount:**
In the department summary aggregation, the pipeline starts directly on the `employees` collection (filtering by `joined_on <= month_end`) and does a `$lookup` to join records from `attendance_logs`. Employees who haven't logged any attendance still pass through the pipeline, contributing 1 to the department's headcount and 0 to present days and late counts.

5. **100x Data Change:**
At 100x data scale (e.g., 10M+ records), running real-time aggregations across collections on every request would become a bottleneck. I would pre-aggregate daily and monthly department stats using MongoDB Change Streams or background scheduled rollup jobs. For storage, I would shard `attendance_logs` using a compound shard key like `{emp_code: "hashed", date: 1}` to distribute write throughput evenly across shards while keeping range queries performant.
