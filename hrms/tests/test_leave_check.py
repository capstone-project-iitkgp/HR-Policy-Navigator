"""check_leave_request: policy validation. 'Today' is Monday 2026-11-16 (set in conftest).

Weekdays used below: Nov 13 Fri, Nov 20 Fri, Nov 23 Mon, Nov 27 Fri, Nov 30 Mon,
Dec 1 Tue, Dec 4 Fri, Dec 7 Mon, Dec 24 Thu, Dec 25 Fri (national holiday), Dec 28 Mon.
"""

from contextlib import closing
from datetime import date

from hrms import db, service


def check(actor, code, start, end, half_day=False):
    result = service.check_leave_request(actor, code, start, end, half_day)
    assert result["ok"]  # the call worked; `valid` is the verdict
    return result


def error_codes(result):
    return [e["code"] for e in result["errors"]]


def available(actor, code):
    return service.get_leave_balance(actor, leave_code=code)["balances"][0]["available"]


def test_weekdays_used_in_these_tests_are_right():
    assert date(2026, 11, 16).weekday() == 0 and date(2026, 12, 4).weekday() == 4
    assert date(2026, 12, 25).weekday() == 4 and date(2026, 11, 13).weekday() == 4


def test_valid_request_reports_days_balance_and_approver():
    r = check("D010", "CL", "2026-12-02", "2026-12-03")  # Wed-Thu
    assert r["valid"] and r["days"] == 2 and r["debit"] == 2
    assert r["balance_before"] == available("D010", "CL")
    assert r["balance_after"] == r["balance_before"] - 2
    assert r["approver"]["emp_id"] == "D003" and r["rule_refs"] == ["DEF HR Manual 5.2.1, 5.5"]


def test_leave_code_is_case_insensitive():
    assert check("D010", "cl", "2026-12-02", "2026-12-02")["valid"]


def test_checking_does_not_change_anything():
    with closing(db.get_conn()) as conn:
        before = conn.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0]
    check("D010", "CL", "2026-12-02", "2026-12-02")
    with closing(db.get_conn()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0] == before


def test_def_sandwich_rule_counts_the_weekend():
    r = check("D006", "CL", "2026-12-04", "2026-12-07")  # Friday to Monday
    assert r["valid"] and r["days"] == 4
    assert r["sandwiched"] == ["2026-12-05", "2026-12-06"]
    assert [w["code"] for w in r["warnings"]] == ["sandwich"]


def test_bldedu_has_no_sandwich_rule_and_saturday_is_a_working_day():
    r = check("B006", "CL", "2026-12-04", "2026-12-07")  # Fri, Sat, (Sun off), Mon
    assert r["valid"] and r["days"] == 3 and r["sandwiched"] == []


def test_a_holiday_inside_the_range_is_not_counted_and_breaks_the_sandwich():
    r = check("D006", "CL", "2026-12-24", "2026-12-28")  # Thu, Fri=Christmas, weekend, Mon
    assert r["days"] == 2 and r["sandwiched"] == []
    assert ["2026-12-25", "holiday"] in [list(s) for s in r["skipped"]]


def test_range_made_only_of_weekly_offs_is_rejected():
    r = check("D006", "CL", "2026-11-21", "2026-11-22")  # Sat-Sun
    assert not r["valid"] and error_codes(r) == ["no_working_days"]


def test_casual_leave_over_five_days_at_a_stretch():
    r = check("D006", "CL", "2026-11-23", "2026-11-30")  # 6 working days + 2 sandwiched
    assert r["days"] == 8 and "above_maximum" in error_codes(r)


def test_earned_leave_needs_15_days_notice_at_def():
    assert error_codes(check("D009", "EL", "2026-11-25", "2026-11-26")) == ["short_notice"]
    assert check("D009", "EL", "2026-12-07", "2026-12-09")["valid"]  # 21 days ahead


def test_earned_leave_cannot_be_backdated():
    assert "backdated" in error_codes(check("D009", "EL", "2026-11-09", "2026-11-10"))


def test_sick_leave_cannot_be_applied_in_advance_but_can_be_backdated_5_days():
    assert error_codes(check("D010", "SL", "2026-11-18", "2026-11-18")) == ["no_advance"]
    assert check("D010", "SL", "2026-11-13", "2026-11-13")["valid"]       # 3 days ago
    assert "backdated" in error_codes(check("D010", "SL", "2026-11-09", "2026-11-09"))  # 7 days ago


def test_sick_leave_needs_a_certificate_after_two_days_this_year():
    assert check("D020", "SL", "2026-11-13", "2026-11-13")["needs_doc"]       # 2 days already used
    assert not check("D010", "SL", "2026-11-13", "2026-11-13")["needs_doc"]   # 1.5 used


def test_zero_balance_is_rejected():
    r = check("D020", "CL", "2026-12-01", "2026-12-01")
    assert error_codes(r) == ["insufficient_balance"] and "0 days" in r["errors"][0]["message"]


def test_bldedu_insufficient_balance_mentions_leave_without_pay():
    r = check("B017", "COMMUTED", "2026-12-01", "2026-12-02")  # 2 days at 2x = 4, has 2
    assert "insufficient_balance" in error_codes(r) and "Leave Without Pay" in r["errors"][0]["message"]


def test_commuted_leave_is_debited_at_twice_the_days():
    r = check("B017", "COMMUTED", "2026-12-01", "2026-12-01")
    assert r["valid"] and r["days"] == 1 and r["debit"] == 2 and r["balance_after"] == 0
    assert r["needs_doc"] and "debit_factor" in [w["code"] for w in r["warnings"]]


def test_bldedu_earned_leave_needs_a_week_notice():
    assert error_codes(check("B014", "EL", "2026-11-20", "2026-11-20")) == ["short_notice"]
    assert check("B014", "EL", "2026-12-01", "2026-12-01")["valid"]


def test_no_leave_during_the_notice_period():
    assert error_codes(check("D015", "CL", "2026-12-01", "2026-12-01")) == ["on_notice"]
    assert error_codes(check("B018", "CL", "2026-12-01", "2026-12-01")) == ["on_notice"]


def test_leave_type_not_open_to_the_employee():
    assert error_codes(check("D014", "EL", "2026-12-07", "2026-12-07")) == ["not_eligible"]  # probation
    assert error_codes(check("B006", "SL", "2026-12-07", "2026-12-07")) == ["not_eligible"]  # no SL at BLDEDU
    assert error_codes(check("D010", "XYZ", "2026-12-07", "2026-12-07")) == ["not_eligible"]


def test_maternity_counts_calendar_days_and_needs_15_days_and_a_certificate():
    short = check("D016", "MAT", "2026-12-01", "2026-12-14")
    assert short["days"] == 14 and "below_minimum" in error_codes(short)
    ok = check("D016", "MAT", "2026-12-01", "2026-12-15")  # includes a weekend
    assert ok["valid"] and ok["days"] == 15 and ok["needs_doc"]
    assert error_codes(check("D017", "MAT", "2026-12-01", "2026-12-15")) == ["not_eligible"]  # 2 children


def test_half_days():
    r = check("D010", "CL", "2026-12-02", "2026-12-02", half_day=True)
    assert r["valid"] and r["days"] == 0.5
    assert error_codes(check("D009", "EL", "2026-12-07", "2026-12-07", half_day=True)) == ["half_day_not_allowed"]
    assert "half_day_range" in error_codes(check("D010", "CL", "2026-12-02", "2026-12-03", half_day=True))


def test_dates_must_be_valid_and_in_this_year():
    assert error_codes(check("D010", "CL", "02/12/2026", "03/12/2026")) == ["bad_date"]
    assert error_codes(check("D010", "CL", "2026-12-03", "2026-12-02")) == ["bad_date"]
    assert error_codes(check("D010", "CL", "2026-12-30", "2027-01-02")) == ["wrong_year"]


def test_overlap_with_an_existing_request():
    r = check("D024", "CL", "2026-12-08", "2026-12-08")  # D024 has a pending request 7-9 Dec
    assert "overlap" in error_codes(r) and "PENDING" in r["errors"][-1]["message"]


def test_cancelled_and_rejected_requests_do_not_block_their_dates():
    assert check("D019", "CL", "2026-11-23", "2026-11-23")["valid"]  # D019's request there was rejected
    assert check("D018", "CL", "2026-11-24", "2026-11-24")["valid"]  # D018's was cancelled


def test_approver_is_hr_when_there_is_no_manager():
    assert check("D002", "CL", "2026-12-02", "2026-12-02")["approver"]["emp_id"] == "D001"


def test_bldedu_warns_when_leave_is_five_or_more_working_days():
    r = check("B014", "EL", "2026-12-01", "2026-12-07")  # Tue-Mon, Sunday off: 6 working days
    assert r["valid"] and r["days"] == 6
    assert "long_leave" in [w["code"] for w in r["warnings"]]
