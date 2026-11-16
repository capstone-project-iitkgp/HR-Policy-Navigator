"""Drive the Streamlit demo page headlessly (streamlit.testing AppTest) as different people."""

from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from hrms import service

APP = str(Path(__file__).resolve().parent.parent / "demo_app.py")


def open_page(who="D010"):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    if who != "D010":
        at.sidebar.selectbox(key="who").select(who).run()
        assert not at.exception
    return at


def texts(elements):
    return " ".join(e.value for e in elements)


def newest_request(who):
    return max(service.list_requests(who)["requests"], key=lambda r: r["id"])


def fill_form(at, code, start, end, half=False):
    at.selectbox(key="apply_code").select(code)
    at.date_input(key="apply_from").set_value(start)
    at.date_input(key="apply_to").set_value(end)
    at.checkbox(key="apply_half").set_value(half)
    return at


def test_page_loads_and_shows_the_logged_in_person():
    at = open_page()
    assert at.sidebar.selectbox(key="who").value == "D010"
    assert "Arjun Verma" in at.sidebar.markdown[0].value
    assert len(at.dataframe) >= 3  # audit table, balances, holidays


def test_balances_table_matches_the_service():
    at = open_page()
    rows = service.get_leave_balance("D010")["balances"]
    shown = next(df for df in at.dataframe if "Available" in df.value.columns)
    assert sorted(shown.value["Available"].tolist()) == sorted(b["available"] for b in rows)


def test_check_then_save_draft_then_confirm_submit():
    at = fill_form(open_page(), "CL", date(2026, 12, 2), date(2026, 12, 3))
    at.button(key="apply_check").click().run()
    assert "Allowed: 2 day(s) of Casual Leave" in texts(at.success)

    at.button(key="apply_draft").click().run()
    assert "Draft #" in texts(at.success)
    rid = newest_request("D010")["id"]
    assert newest_request("D010")["status"] == "DRAFT"

    at.button(key="submit_%d" % rid).click().run()
    assert "submitted" in texts(at.success)
    assert newest_request("D010")["status"] == "PENDING"


def test_a_policy_failure_is_explained_and_nothing_is_saved():
    before = len(service.list_requests("D020")["requests"])
    at = fill_form(open_page("D020"), "CL", date(2026, 12, 1), date(2026, 12, 1))  # D020 has no casual leave left
    at.button(key="apply_draft").click().run()
    assert "available but this request needs" in texts(at.error)
    assert len(service.list_requests("D020")["requests"]) == before


def test_notice_period_banner_and_refusal():
    at = open_page("D015")
    assert "Serving notice" in texts(at.sidebar.warning)
    fill_form(at, "CL", date(2026, 12, 1), date(2026, 12, 1)).button(key="apply_draft").click().run()
    assert "notice period" in texts(at.error)


def test_required_document_blocks_submit_until_ticked():
    at = fill_form(open_page("D020"), "SL", date(2026, 11, 13), date(2026, 11, 13))
    at.button(key="apply_draft").click().run()
    rid = newest_request("D020")["id"]
    at.button(key="submit_%d" % rid).click().run()
    assert "Medical certificate" in texts(at.error) and newest_request("D020")["status"] == "DRAFT"
    at.checkbox(key="doc_%d" % rid).set_value(True)
    at.button(key="submit_%d" % rid).click().run()
    assert newest_request("D020")["status"] == "PENDING"


def test_withdraw_a_pending_request():
    at = open_page("D024")  # has a pending request
    rid = service.list_requests("D024", status="PENDING")["requests"][0]["id"]
    at.button(key="cancel_%d" % rid).click().run()
    assert "cancelled" in texts(at.success)
    assert service.list_requests("D024", status="PENDING")["requests"] == []


def test_change_dates_of_a_draft():
    at = open_page()
    rid = service.list_requests("D010", status="DRAFT")["requests"][0]["id"]  # seeded draft, 1 Dec
    at.date_input(key="mod_to_%d" % rid).set_value(date(2026, 12, 2))
    at.button(key="modify_%d" % rid).click().run()
    assert "changed" in texts(at.success)
    assert service.list_requests("D010", status="DRAFT")["requests"][0]["days"] == 2


def test_manager_sees_and_approves_the_pending_request():
    at = open_page("D003")
    rid = service.list_pending_approvals("D003")["approvals"][0]["id"]
    assert "Waiting for your decision: 1" in texts(at.subheader)
    at.button(key="approve_%d" % rid).click().run()
    assert "approved" in texts(at.success)
    assert service.list_requests("D024", status="APPROVED")["requests"][0]["id"] == rid


def test_rejecting_needs_a_note():
    at = open_page("D003")
    rid = service.list_pending_approvals("D003")["approvals"][0]["id"]
    at.button(key="reject_%d" % rid).click().run()
    assert "reason" in texts(at.error)
    at.text_input(key="note_%d" % rid).set_value("Release week")
    at.button(key="reject_%d" % rid).click().run()
    assert "rejected" in texts(at.success)


def test_someone_with_no_reports_has_an_empty_inbox():
    at = open_page("D006")
    assert "Waiting for your decision: 0" in texts(at.subheader)
    assert not [b for b in at.button if b.key and b.key.startswith("approve_")]


def test_reset_restores_the_seed_data():
    at = fill_form(open_page(), "CL", date(2026, 12, 2), date(2026, 12, 2))
    at.button(key="apply_draft").click().run()
    assert len(service.list_requests("D010")["requests"]) == 2
    at.button(key="reset").click().run()
    assert "reset" in texts(at.success) and len(service.list_requests("D010")["requests"]) == 1


def test_audit_table_in_the_sidebar_lists_actions():
    at = fill_form(open_page(), "CL", date(2026, 12, 2), date(2026, 12, 2))
    at.button(key="apply_draft").click().run()
    audit = next(df for df in at.dataframe if "action" in df.value.columns)
    assert "create_leave_draft" in audit.value["action"].tolist()
