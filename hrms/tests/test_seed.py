"""The seeded database is complete, consistent and the same every time."""

from contextlib import closing

from hrms import db, rules, seed


def rows(sql, args=()):
    with closing(db.get_conn()) as conn:  # first call builds and seeds the DB
        return [dict(r) for r in conn.execute(sql, args)]


def test_database_is_built_automatically_when_missing():
    assert not db.db_path().exists()
    assert rows("SELECT COUNT(*) AS n FROM employees")[0]["n"] == 48
    assert db.db_path().exists()


def test_24_employees_per_org_with_one_hr_user_each():
    by_org = {r["policy_org"]: r["n"] for r in rows(
        "SELECT policy_org, COUNT(*) AS n FROM employees GROUP BY policy_org")}
    assert by_org == {"DEF": 24, "BLDEDU": 24}
    hr = {r["policy_org"] for r in rows("SELECT policy_org FROM employees WHERE role = 'hr'")}
    assert hr == {"DEF", "BLDEDU"}


def test_every_manager_exists_and_stays_in_the_same_org():
    bad = rows("SELECT e.emp_id FROM employees e JOIN employees m ON m.emp_id = e.manager_id "
               "WHERE m.policy_org != e.policy_org")
    assert bad == []
    assert rows("SELECT COUNT(*) AS n FROM employees WHERE manager_id IS NULL")[0]["n"] == 2


def test_one_employee_per_org_is_serving_notice_with_a_start_date():
    got = rows("SELECT emp_id, notice_start FROM employees WHERE status = 'notice' ORDER BY emp_id")
    assert [g["emp_id"] for g in got] == ["B018", "D015"]
    assert all(g["notice_start"] for g in got)
    assert rows("SELECT COUNT(*) AS n FROM employees WHERE status = 'active' AND notice_start IS NOT NULL")[0]["n"] == 0


def test_categories_belong_to_the_employees_org():
    for e in rows("SELECT policy_org, category FROM employees"):
        assert e["category"] in rules.CATEGORIES[e["policy_org"]]


def test_no_balance_is_negative():
    bad = rows("SELECT emp_id, leave_code FROM leave_balances "
               "WHERE entitled + carried_forward - used - pending < 0")
    assert bad == []


def test_carry_forward_respects_the_cap():
    for b in rows("SELECT b.emp_id, b.leave_code, b.carried_forward, e.policy_org "
                  "FROM leave_balances b JOIN employees e USING (emp_id)"):
        cap = rules.LEAVES[b["policy_org"]][b["leave_code"]]["carry_cap"]
        assert b["carried_forward"] <= cap, b
        if b["policy_org"] == "DEF" and b["leave_code"] == "EL":
            # DEF 5.2.3: carried forward plus the current month's credit must not exceed 150
            assert b["carried_forward"] + 1.25 <= cap, b


def test_only_eligible_employees_have_a_balance():
    for b in rows("SELECT b.leave_code, e.* FROM leave_balances b JOIN employees e USING (emp_id)"):
        assert rules.eligible(b, b["leave_code"])[0], b


def test_pending_equals_the_sum_of_pending_requests():
    for b in rows("SELECT emp_id, leave_code, pending FROM leave_balances"):
        expected = rows("SELECT COALESCE(SUM(days), 0) AS d FROM leave_requests "
                        "WHERE emp_id = ? AND leave_code = ? AND status = 'PENDING'",
                        (b["emp_id"], b["leave_code"]))[0]["d"]
        assert b["pending"] == expected


def test_every_request_status_is_represented():
    statuses = {r["status"] for r in rows("SELECT status FROM leave_requests")}
    assert statuses == {"DRAFT", "PENDING", "APPROVED", "REJECTED", "SENT_BACK", "CANCELLED"}


def test_holiday_calendars():
    counts = {(r["org"], r["type"]): r["n"] for r in rows(
        "SELECT org, type, COUNT(*) AS n FROM holidays GROUP BY org, type")}
    assert counts[("DEF", "national")] == 10 and counts[("DEF", "restricted")] == 5
    assert counts[("BLDEDU", "compulsory")] == 3 and counts[("BLDEDU", "public")] == 12


def test_seeding_twice_gives_identical_data():
    first = rows("SELECT * FROM leave_balances ORDER BY emp_id, leave_code")
    seed.build()
    second = rows("SELECT * FROM leave_balances ORDER BY emp_id, leave_code")
    assert first == second


def test_edge_case_employees_from_the_eval_set_are_present():
    # Q001 / Q007 joiners, Q029 tenure pair, a zero casual-leave balance
    got = {r["emp_id"]: r for r in rows(
        "SELECT emp_id, join_date, policy_org FROM employees WHERE emp_id IN "
        "('D010','B010','D012','D013')")}
    assert got["D010"]["join_date"] == "2026-03-18" and got["B010"]["join_date"] == "2026-03-18"
    assert got["D012"]["join_date"] > "2024-11-16" and got["D013"]["join_date"] < "2024-11-16"
    zero = rows("SELECT entitled + carried_forward - used - pending AS a FROM leave_balances "
                "WHERE emp_id = 'D020' AND leave_code = 'CL'")
    assert zero[0]["a"] == 0
