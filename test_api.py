import threading
from datetime import datetime, date, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app, db, ensure_indexes

client = TestClient(app)

def setup_module():
    import subprocess
    subprocess.run(["python", "sample_seed.py"], check=True)
    ensure_indexes()

def test_health():
    res = client.get("/health")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}

def test_create_and_list_employee():
    emp_payload = {
        "emp_code": "EMP9901",
        "name": "Test Tester",
        "email": "tester@example.com",
        "department": "QA",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-05-01"
    }
    db.employees.delete_one({"emp_code": "EMP9901"})
    res = client.post("/employees", json=emp_payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["emp_code"] == "EMP9901"
    assert isinstance(data["created_at"], int)
    assert "_id" not in data

    # Duplicate -> 409
    res_dup = client.post("/employees", json=emp_payload)
    assert res_dup.status_code == 409

    # Identical shift times -> 422
    bad_payload = dict(emp_payload, emp_code="EMP9902", shift_end="09:30")
    res_bad = client.post("/employees", json=bad_payload)
    assert res_bad.status_code == 422

    # List employees
    res_list = client.get("/employees?department=QA")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["total"] >= 1
    assert any(e["emp_code"] == "EMP9901" for e in list_data["items"])

def test_punch_in_and_out_and_rules():
    emp_code = "EMP9901"
    db.attendance_logs.delete_many({"emp_code": emp_code})

    # Test punch-in on 2026-07-06 at 09:40:01 IST (10 minutes late!)
    dt_utc = datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc)
    ts_ms = int(dt_utc.timestamp() * 1000)

    # Seconds-looking timestamp (rejected with 422)
    res_sec = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": 1783311001})
    assert res_sec.status_code == 422

    # Valid punch in
    res = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_ms, "status": "PRESENT"})
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["date"] == "2026-07-06"
    assert data["late_minutes"] == 10
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["half_day"] is False
    assert "_id" not in data
    assert "id" not in data

    # Duplicate punch in same day -> 409
    res_dup = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_ms})
    assert res_dup.status_code == 409

    # Punch out at 19:15:00 IST = 13:45:00 UTC (45m overtime!)
    out_utc = datetime(2026, 7, 6, 13, 45, 0, tzinfo=timezone.utc)
    out_ms = int(out_utc.timestamp() * 1000)

    res_out = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": out_ms})
    assert res_out.status_code == 200, res_out.text
    out_data = res_out.json()
    assert out_data["work_hours"] == 9.58
    assert out_data["overtime_minutes"] == 45
    assert out_data["half_day"] is False

    # Duplicate punch out -> 409
    res_out_dup = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": out_ms})
    assert res_out_dup.status_code == 409

def test_overnight_shift():
    emp_code = "EMP9903"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    res = client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Night Owl",
        "email": "owl@example.com",
        "department": "Security",
        "shift_start": "22:00",
        "shift_end": "06:00",
        "joined_on": "2026-01-01"
    })
    assert res.status_code == 201

    # Punch in at 01:10 IST on 2026-07-07 (< 06:00) -> belongs to 2026-07-06 shift!
    in_utc = datetime(2026, 7, 6, 19, 40, 0, tzinfo=timezone.utc)
    in_ms = int(in_utc.timestamp() * 1000)

    res_in = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": in_ms})
    assert res_in.status_code == 201
    assert res_in.json()["date"] == "2026-07-06"

    # Punch out at 06:40 IST on 2026-07-07 = 01:10 UTC on 2026-07-07
    out_utc = datetime(2026, 7, 7, 1, 10, 0, tzinfo=timezone.utc)
    out_ms = int(out_utc.timestamp() * 1000)

    res_out = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": out_ms})
    assert res_out.status_code == 200
    out_data = res_out.json()
    assert out_data["date"] == "2026-07-06"
    assert out_data["overtime_minutes"] == 40

def test_regularize_patch():
    in_utc = datetime(2026, 7, 7, 4, 45, 0, tzinfo=timezone.utc)
    in_ms = int(in_utc.timestamp() * 1000)

    patch_payload = {
        "punch_in": in_ms,
        "reason": "Traffic jam verified by manager",
        "regularized_by": "manager.bob"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=patch_payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["late_minutes"] == 45
    assert len(data["history"]) >= 1
    h = data["history"][-1]
    assert h["by"] == "manager.bob"
    assert "late_minutes" in h["changes"]
    assert "work_hours" in h["changes"]

def test_analytics_employee_monthly():
    # EMP0001 for 2026-07
    res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] == 23
    assert data["present_days"] >= 2.0
    assert isinstance(data["attendance_pct"], float)

def test_analytics_department_summary():
    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["month"] == "2026-07"
    depts = {item["department"]: item for item in data["items"]}
    assert "Engineering" in depts
    assert depts["Engineering"]["headcount"] >= 2
    assert "Sales" in depts
    assert depts["Sales"]["late_count"] >= 1

def test_analytics_leaderboard_late_and_ties():
    # Seed a tie scenario for EMP9904 and EMP9905 with same late minutes
    db.employees.delete_many({"emp_code": {"$in": ["EMP9904", "EMP9905"]}})
    db.attendance_logs.delete_many({"emp_code": {"$in": ["EMP9904", "EMP9905"]}})

    client.post("/employees", json={
        "emp_code": "EMP9904", "name": "Tie User A", "email": "a@example.com",
        "department": "TieDept", "joined_on": "2026-01-01"
    })
    client.post("/employees", json={
        "emp_code": "EMP9905", "name": "Tie User B", "email": "b@example.com",
        "department": "TieDept", "joined_on": "2026-01-01"
    })

    # Both punch in on 2026-07-15 at 10:15 IST (45m late)
    in_utc = datetime(2026, 7, 15, 4, 45, 0, tzinfo=timezone.utc)
    ts = int(in_utc.timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": "EMP9904", "punched_at": ts})
    client.post("/attendance/punch-in", json={"emp_code": "EMP9905", "punched_at": ts})

    res = client.get("/analytics/leaderboard/late?month=2026-07&department=TieDept&limit=1")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    # Both should have rank 1 and both should be returned because rank <= limit!
    assert len(items) == 2, f"Expected 2 tied employees at rank 1, got {items}"
    assert items[0]["rank"] == 1
    assert items[1]["rank"] == 1
    assert items[0]["emp_code"] == "EMP9904"
    assert items[1]["emp_code"] == "EMP9905"

def test_analytics_department_trend():
    res = client.get("/analytics/departments/Engineering/trend?from=2026-07-05&to=2026-07-15")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["department"] == "Engineering"
    assert len(data["items"]) == 11
    # Check weekend
    assert data["items"][0]["date"] == "2026-07-05"
    assert data["items"][0]["is_working_day"] is False
    assert data["items"][0]["attendance_rate"] is None

def test_explain_endpoints():
    endpoints = ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]
    for ep in endpoints:
        params = {
            "emp_code": "EMP0001",
            "month": "2026-07",
            "department": "Engineering",
            "from": "2026-07-01",
            "to": "2026-07-10",
            "limit": 10
        }
        res = client.get(f"/admin/explain/{ep}", params=params)
        assert res.status_code == 200, f"Explain failed for {ep}: {res.text}"
        data = res.json()
        assert data["endpoint"] == ep
        assert "explain" in data
        assert "collection" in data

def test_concurrency_punch_in():
    emp_code = "EMP9901"
    test_date_utc = datetime(2026, 8, 2, 4, 0, 0, tzinfo=timezone.utc)
    ts_ms = int(test_date_utc.timestamp() * 1000)

    results = []
    def do_punch():
        r = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ts_ms})
        results.append(r.status_code)

    threads = [threading.Thread(target=do_punch) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    count_201 = results.count(201)
    count_409 = results.count(409)
    assert count_201 == 1, f"Expected exactly one 201, got {count_201} in {results}"
    assert count_409 == 7, f"Expected seven 409s, got {count_409} in {results}"

def test_grace_period_and_overtime_exact_boundaries():
    emp_code = "EMP9906"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code, "name": "Boundary Tester", "email": "bound@example.com",
        "department": "QA", "shift_start": "09:30", "shift_end": "18:30", "joined_on": "2026-01-01"
    })

    # Exact 10:00 grace boundary: 09:40:00 IST -> late_minutes = 0
    t_094000 = int(datetime(2026, 8, 3, 4, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    r1 = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": t_094000})
    assert r1.status_code == 201
    assert r1.json()["late_minutes"] == 0

    # Overtime boundary: 29 minutes -> 0 overtime
    t_185900 = int(datetime(2026, 8, 3, 13, 29, 0, tzinfo=timezone.utc).timestamp() * 1000)
    r_out1 = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": t_185900})
    assert r_out1.status_code == 200
    assert r_out1.json()["overtime_minutes"] == 0

    # Next day: 09:40:01 IST -> strictly > 10:00 grace -> late_minutes = 10
    t_094001 = int(datetime(2026, 8, 4, 4, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    r2 = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": t_094001})
    assert r2.status_code == 201
    assert r2.json()["late_minutes"] == 10

    # Overtime boundary: exact 30 minutes -> 30 overtime
    t_190000 = int(datetime(2026, 8, 4, 13, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
    r_out2 = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": t_190000})
    assert r_out2.status_code == 200
    assert r_out2.json()["overtime_minutes"] == 30

    # Half day boundary: punch in 09:30, punch out 13:58 (4h 28m = 4.47 hours < 4.50) -> half_day = True
    t_093000 = int(datetime(2026, 8, 5, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    r3 = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": t_093000})
    assert r3.status_code == 201
    t_135800 = int(datetime(2026, 8, 5, 8, 28, 0, tzinfo=timezone.utc).timestamp() * 1000)
    r_out3 = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": t_135800})
    assert r_out3.status_code == 200
    assert r_out3.json()["work_hours"] == 4.47
    assert r_out3.json()["half_day"] is True

def test_mid_month_joiner_working_days():
    emp_code = "EMP9907"
    db.employees.delete_one({"emp_code": emp_code})
    # Joins on 2026-07-20 (Monday) -> Exactly 10 working weekdays in July 2026
    client.post("/employees", json={
        "emp_code": emp_code, "name": "Mid Month Joiner", "email": "joiner@example.com",
        "department": "Engineering", "joined_on": "2026-07-20"
    })

    res = client.get(f"/analytics/employees/{emp_code}/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 10, f"Expected 10 working days, got {data['working_days']}"

def test_regularize_status_transitions():
    emp_code = "EMP9901"
    # Ensure there is a record on 2026-07-06
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": "2026-07-06"})
    assert rec is not None

    # 1. Changing to LEAVE must nullify punches and zero derived fields
    patch_leave = {
        "status": "LEAVE",
        "reason": "Applied medical leave post-facto",
        "regularized_by": "hr.lead"
    }
    res_l = client.patch(f"/attendance/{emp_code}/2026-07-06", json=patch_leave)
    assert res_l.status_code == 200, res_l.text
    d_l = res_l.json()
    assert d_l["status"] == "LEAVE"
    assert d_l["punch_in"] is None
    assert d_l["punch_out"] is None
    assert d_l["work_hours"] is None
    assert d_l["late_minutes"] == 0
    assert d_l["overtime_minutes"] == 0
    assert d_l["half_day"] is False

    # 2. Supplying punch times when setting to LEAVE -> 422
    patch_bad = {
        "status": "LEAVE",
        "punch_in": 1783311001000,
        "reason": "Invalid punch with leave",
        "regularized_by": "hr.lead"
    }
    res_bad = client.patch(f"/attendance/{emp_code}/2026-07-06", json=patch_bad)
    assert res_bad.status_code == 422

    # 3. Request that changes nothing -> 422
    res_noop = client.patch(f"/attendance/{emp_code}/2026-07-06", json=patch_leave)
    assert res_noop.status_code == 422

def test_concurrency_punch_out():
    emp_code = "EMP9908"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    client.post("/employees", json={
        "emp_code": emp_code, "name": "Conc Out Tester", "email": "cout@example.com",
        "department": "QA", "shift_start": "09:30", "shift_end": "18:30", "joined_on": "2026-01-01"
    })

    in_ms = int(datetime(2026, 8, 10, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": in_ms})

    out_ms = int(datetime(2026, 8, 10, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    results = []

    def do_out():
        r = client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": out_ms})
        results.append(r.status_code)

    threads = [threading.Thread(target=do_out) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(200) == 1, f"Expected 1 200, got {results}"
    assert results.count(409) == 5, f"Expected 5 409s, got {results}"

def test_explain_ixscan_verification():
    res = client.get("/admin/explain/attendance_list?emp_code=EMP0001")
    assert res.status_code == 200
    explain_json = str(res.json()["explain"])
    # Verify IXSCAN appears in the execution stats / query plan
    assert "IXSCAN" in explain_json, "Expected index scan IXSCAN in explain plan"

if __name__ == "__main__":
    setup_module()
    test_health()
    print("Health passed!")
    test_create_and_list_employee()
    print("Employee create & list passed!")
    test_punch_in_and_out_and_rules()
    print("Punch in/out passed!")
    test_overnight_shift()
    print("Overnight shift passed!")
    test_regularize_patch()
    print("Regularize patch passed!")
    test_grace_period_and_overtime_exact_boundaries()
    print("Exact grace and overtime boundaries passed!")
    test_mid_month_joiner_working_days()
    print("Mid-month joiner working days passed!")
    test_regularize_status_transitions()
    print("Regularize status transitions passed!")
    test_analytics_employee_monthly()
    print("Employee monthly analytics passed!")
    test_analytics_department_summary()
    print("Department summary analytics passed!")
    test_analytics_leaderboard_late_and_ties()
    print("Leaderboard ties & ranking passed!")
    test_analytics_department_trend()
    print("Department trend passed!")
    test_explain_endpoints()
    print("Explain endpoints passed!")
    test_explain_ixscan_verification()
    print("Explain IXSCAN verification passed!")
    test_concurrency_punch_in()
    print("Concurrency punch-in test passed!")
    test_concurrency_punch_out()
    print("Concurrency punch-out test passed!")
    print("\n>>> ALL TESTS PASSED WITH 100% SUCCESS! <<<")

