"""Streamlit demo of the dummy HRMS: employee screens and the manager inbox.

    python -m streamlit run hrms/demo_app.py        (from the Code/ folder)

"Log in as" is a demo login: pick anyone. The page calls hrms/service.py directly. The only places
that pass `confirmed=True` are the Confirm buttons below, so leave is only filed, cancelled or
changed when a person clicks. The agent never gets those buttons.
"""

import sys
from contextlib import closing
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `hrms` imports under `streamlit run`

import streamlit as st  # noqa: E402

from hrms import db, seed, service  # noqa: E402

st.set_page_config(page_title="Dummy HRMS", page_icon=":calendar:", layout="wide")
WRITE_ACTIONS = ("create_leave_draft", "submit_request", "cancel_request", "modify_request", "decide_request")
EDITABLE = ("DRAFT", "PENDING", "APPROVED", "SENT_BACK")  # statuses whose dates can still be changed


# --- small helpers -------------------------------------------------------
def flash(kind, text):
    """Show a message after the page reruns."""
    st.session_state["flash"] = (kind, text)


def show_failure(result):
    """Show why a service call failed (the list of rule errors, or the single message)."""
    for e in result.get("errors") or [{"message": result["message"]}]:
        st.error(e["message"])


def done(result, success_text):
    """Turn a service result into a message and refresh the page."""
    if result["ok"]:
        flash("success", success_text)
    else:
        flash("error", " ".join(e["message"] for e in result.get("errors") or [result]))
    st.rerun()


def show_check(c):
    """Display the outcome of a policy check."""
    if c["valid"]:
        approver = c["approver"]["name"] if c.get("approver") else "HR"
        st.success("Allowed: %g day(s) of %s. Balance after: %g. Approver: %s." % (
            c["days"], c["leave_name"], c["balance_after"], approver))
    for e in c["errors"]:
        st.error(e["message"])
    for w in c["warnings"]:
        st.warning(w["message"])
    if c.get("needs_doc"):
        st.info("Document needed: %s" % c["doc_note"])


# --- who is logged in ----------------------------------------------------
with closing(db.get_conn()) as conn:
    people = [dict(r) for r in conn.execute(
        "SELECT emp_id, name, policy_org, role FROM employees ORDER BY emp_id")]
labels = {p["emp_id"]: "%s  %s  (%s, %s)" % (p["emp_id"], p["name"], p["policy_org"], p["role"]) for p in people}
who = st.sidebar.selectbox("Log in as (demo login)", list(labels), format_func=labels.get,
                           index=list(labels).index("D010"), key="who")
me = service.get_employee(who)["employee"]
manager = service.get_manager(who).get("manager")
st.sidebar.markdown("**%s**  \n%s, %s  \n%s - %s  \nJoined %s  \nManager: %s" % (
    me["name"], me["designation"], me["department"], me["policy_org"], me["category"], me["join_date"],
    manager["name"] if manager else "none"))
if me["status"] == "notice":
    st.sidebar.warning("Serving notice since %s: no leave can be applied for." % me["notice_start"])
st.sidebar.caption("Today in this demo: %s" % db.today().isoformat())

with st.sidebar.expander("Demo tools"):
    if st.button("Reset demo data", key="reset"):
        seed.build()
        flash("success", "Demo data reset to the seed files.")
        st.rerun()
    st.caption("Recent actions (audit log)")
    with closing(db.get_conn()) as conn:
        audit = [dict(r) for r in conn.execute(
            "SELECT ts, actor, channel, action, request_id, ok FROM audit_log WHERE action IN (%s) ORDER BY id DESC LIMIT 10"
            % ",".join("?" * len(WRITE_ACTIONS)), WRITE_ACTIONS)]
    st.dataframe(audit, hide_index=True)

st.title("Dummy HRMS: leave management")
if "flash" in st.session_state:
    kind, text = st.session_state.pop("flash")
    getattr(st, kind)(text)

approvals = service.list_pending_approvals(who)["approvals"]
tab_balances, tab_apply, tab_requests, tab_holidays, tab_approvals = st.tabs(
    ["My balances", "Apply for leave", "My requests", "Holidays", "Approvals"])  # fixed labels keep the selected tab

# --- balances ------------------------------------------------------------
balances = service.get_leave_balance(who)["balances"]
with tab_balances:
    st.dataframe([{
        "Leave": "%s (%s)" % (b["leave_name"], b["leave_code"]), "Credited so far": b["entitled"],
        "Carried forward": b["carried_forward"], "Used": b["used"], "Pending approval": b["pending"],
        "Available": b["available"], "Policy": b["source_ref"]} for b in balances], hide_index=True)

# --- apply ---------------------------------------------------------------
with tab_apply:
    names = {b["leave_code"]: b["leave_name"] for b in balances}
    code = st.selectbox("Leave type", list(names), format_func=lambda c: "%s - %s" % (c, names[c]), key="apply_code")
    left, right = st.columns(2)
    start = left.date_input("From", value=db.today(), key="apply_from")
    end = right.date_input("To", value=start, key="apply_to")
    half = st.checkbox("Half day", key="apply_half")
    reason = st.text_input("Reason (optional)", key="apply_reason")
    check_col, draft_col = st.columns(2)
    if check_col.button("Check against policy", key="apply_check"):
        show_check(service.check_leave_request(who, code, start.isoformat(), end.isoformat(), half))
    if draft_col.button("Save as draft", key="apply_draft"):
        saved = service.create_leave_draft(who, code, start.isoformat(), end.isoformat(), half, reason or None)
        if saved["ok"]:
            flash("success", "Draft #%d saved. Open it under My requests and confirm to submit." % saved["request_id"])
            st.rerun()
        show_failure(saved)

# --- my requests ---------------------------------------------------------
def request_card(r):
    rid, status = r["id"], r["status"]
    title = "#%d  %s  %s to %s  (%g days)  -  %s" % (rid, r["leave_code"], r["start_date"], r["end_date"], r["days"], status)
    with st.expander(title, expanded=status in ("DRAFT", "SENT_BACK")):
        if r["reason"]:
            st.caption("Reason: %s" % r["reason"])
        if r["decision_note"]:
            st.caption("Note: %s (%s, %s)" % (r["decision_note"], r["decided_by"], r["decided_on"]))
        if status in ("DRAFT", "SENT_BACK"):
            preview = service.preview_request(who, rid)
            show_check(preview)
            doc = st.checkbox("I have attached the required document", key="doc_%d" % rid) if preview.get("needs_doc") else False
            if st.button("Confirm and submit", key="submit_%d" % rid):
                done(service.submit_request(who, rid, confirmed=True, doc_attached=doc), "Request #%d submitted." % rid)
            if st.button("Discard", key="discard_%d" % rid):
                done(service.cancel_request(who, rid), "Request #%d discarded." % rid)
        if status in ("PENDING", "APPROVED") and st.button(
                "Withdraw request" if status == "PENDING" else "Cancel approved leave", key="cancel_%d" % rid):
            done(service.cancel_request(who, rid, confirmed=True), "Request #%d cancelled." % rid)
        if status in EDITABLE:
            if status == "APPROVED":
                st.caption("Changing approved leave sends it back to your manager for a new approval.")
            a, b = st.columns(2)
            new_start = a.date_input("New from", value=date.fromisoformat(r["start_date"]), key="mod_from_%d" % rid)
            new_end = b.date_input("New to", value=date.fromisoformat(r["end_date"]), key="mod_to_%d" % rid)
            if st.button("Change dates", key="modify_%d" % rid):
                done(service.modify_request(who, rid, new_start.isoformat(), new_end.isoformat(), confirmed=True),
                     "Request #%d changed." % rid)


with tab_requests:
    mine = service.list_requests(who)["requests"]
    if not mine:
        st.info("No leave requests yet.")
    for r in mine:
        request_card(r)

# --- holidays ------------------------------------------------------------
with tab_holidays:
    listing = service.get_holidays(who)
    if listing["note"]:
        st.caption(listing["note"])
    st.dataframe(listing["holidays"], hide_index=True)

# --- approvals -----------------------------------------------------------
with tab_approvals:
    st.subheader("Waiting for your decision: %d" % len(approvals))
    if not approvals:
        st.info("Nothing waiting for your decision.")
    for a in approvals:
        rid, bal = a["id"], a["balance"]
        title = "#%d  %s  %s  %s to %s  (%g days)" % (rid, a["employee_name"], a["leave_code"], a["start_date"], a["end_date"], a["days"])
        with st.expander(title, expanded=True):
            st.write("%s, %s. Reason: %s. Document attached: %s." % (
                a["designation"], a["department"], a["reason"] or "none given", "yes" if a["doc_attached"] else "no"))
            st.caption("Balance for %s: credited %g + carried %g, used %g, pending %g (including this request)." % (
                a["leave_code"], bal["entitled"], bal["carried_forward"], bal["used"], bal["pending"]))
            note = st.text_input("Note to the employee (needed to reject or send back)", key="note_%d" % rid)
            ok_col, no_col, back_col = st.columns(3)
            if ok_col.button("Approve", key="approve_%d" % rid):
                done(service.decide_request(who, rid, "APPROVE", note), "Request #%d approved." % rid)
            if no_col.button("Reject", key="reject_%d" % rid):
                done(service.decide_request(who, rid, "REJECT", note), "Request #%d rejected." % rid)
            if back_col.button("Send back", key="sendback_%d" % rid):
                done(service.decide_request(who, rid, "SEND_BACK", note), "Request #%d sent back." % rid)
