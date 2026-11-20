"""HR Policy Navigator: chat page (H8).

    cd Code
    read -s API_KEY; export API_KEY          # key never goes in a file
    export CHAT_BASE_URL=... CHAT_MODEL=Qwen/Qwen3-8B CAPSTONE_CA_CERT=...
    .venv/bin/streamlit run app/chat_app.py

"Log in as" is the demo login of the dummy HR system: the chosen person is the acting employee. It is
passed to the agent as ``employee_id`` and nothing the model says can change it. The page calls the
agent in the same process (no API). Policy search uses ``hr_navigator.retrieval.search`` once it
exists; until then policy questions find no passages and the agent says the documents do not say.
"""

import importlib
import sys
import uuid
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]  # `hr_navigator` and `hrms` import under `streamlit run`

import streamlit as st  # noqa: E402

from hr_navigator.agents.orchestrator import Agent  # noqa: E402
from hr_navigator.agents.toolbox import ToolBox  # noqa: E402
from hr_navigator.observability import tracing  # noqa: E402
from hr_navigator.schemas import AgentRequest  # noqa: E402
from hr_navigator.state.session_store import SessionStore  # noqa: E402
from hrms import db, service  # noqa: E402

st.set_page_config(page_title="HR Policy Navigator", page_icon=":speech_balloon:", layout="wide")

NO_RETRIEVAL = "Policy search is not available yet, so policy questions will not find any text."


def find_search():
    """The real retrieval if Akash's module exists, else a search that finds nothing."""
    try:
        fn = getattr(importlib.import_module("hr_navigator.retrieval"), "search", None)
    except ImportError:
        fn = None
    return (fn, True) if fn else ((lambda query, k=5, filters=None: []), False)


def new_chat(who: str) -> None:
    st.session_state.update(messages=[], session_id=f"ui-{uuid.uuid4().hex[:8]}", chat_for=who)


# --- who is logged in (same demo login as hrms/demo_app.py) ----------------
with closing(db.get_conn()) as conn:
    people = [dict(r) for r in conn.execute(
        "SELECT emp_id, name, policy_org, role FROM employees ORDER BY emp_id")]
labels = {p["emp_id"]: "%s  %s  (%s, %s)" % (p["emp_id"], p["name"], p["policy_org"], p["role"]) for p in people}
who = st.sidebar.selectbox("Log in as (demo login)", list(labels), format_func=labels.get,
                           index=list(labels).index("D010"), key="who")
me = service.get_employee(who)["employee"]
manager = service.get_manager(who).get("manager")
st.sidebar.markdown("**%s**  \n%s, %s  \n%s - %s  \nManager: %s" % (
    me["name"], me["designation"], me["department"], me["policy_org"], me["category"],
    manager["name"] if manager else "none"))
if me["status"] == "notice":
    st.sidebar.warning("Serving notice since %s: no leave can be applied for." % me["notice_start"])
st.sidebar.caption("Today in this demo: %s" % db.today().isoformat())

if st.session_state.get("chat_for") != who:  # another person logged in: a new, empty chat
    new_chat(who)
if st.sidebar.button("New chat", key="new_chat"):
    new_chat(who)

record = st.sidebar.checkbox("Record traces", value=True, key="record_traces",
                             help="Writes spans to .cache/traces; view them with app/trace_page.py")
search, have_search = find_search()
if not have_search:
    st.sidebar.warning(NO_RETRIEVAL)

tracer = tracing.configure(enabled=record)
agent = Agent(ToolBox(search), store=SessionStore(), critic="advisory", tracer=tracer)


# --- drafts: the only place anything is filed or discarded ------------------
def drafts_of(emp: str) -> dict[int, dict]:
    return {r["id"]: r for r in service.list_requests(emp, status="DRAFT")["requests"]}


def flash(kind: str, text: str) -> None:
    st.session_state["flash"] = (kind, text)
    st.rerun()


def show_check(c: dict) -> None:
    if c.get("valid"):
        approver = c["approver"]["name"] if c.get("approver") else "HR"
        st.success("Allowed: %g day(s) of %s. Balance after: %g. Approver: %s." % (
            c["days"], c["leave_name"], c["balance_after"], approver))
    for e in c.get("errors", []):
        st.error(e["message"])
    for w in c.get("warnings", []):
        st.warning(w["message"])


def draft_card(rid: int, key: str) -> None:
    """Confirm or discard one draft. Only these two buttons pass ``confirmed=True`` / cancel; the agent has no such tool."""
    req = next((r for r in service.list_requests(who)["requests"] if r["id"] == rid), None)
    if req is None:
        return
    title = "Draft #%d: %s, %s to %s (%g days)" % (rid, req["leave_code"], req["start_date"], req["end_date"], req["days"])
    if req["status"] != "DRAFT":
        st.caption("%s: now %s." % (title, req["status"].replace("CANCELLED", "discarded").lower()))
        return
    with st.container(border=True):
        st.markdown("**%s**  \nNothing is submitted until you confirm." % title)
        preview = service.preview_request(who, rid)
        show_check(preview)
        doc = st.checkbox("I have attached the required document (%s)" % preview.get("doc_note", ""),
                          key="doc_%s" % key) if preview.get("needs_doc") else False
        left, right = st.columns(2)
        if left.button("Confirm and submit", key="submit_%s" % key, type="primary"):
            res = service.submit_request(who, rid, confirmed=True, doc_attached=doc)
            if res["ok"]:
                flash("success", "Request #%d submitted for approval." % rid)
            flash("error", " ".join(e["message"] for e in res.get("errors") or [res]))
        if right.button("Discard", key="discard_%s" % key):
            res = service.cancel_request(who, rid)
            flash("success" if res["ok"] else "error", "Request #%d discarded." % rid if res["ok"] else res["message"])


# --- one message ------------------------------------------------------------
def show_assistant(m: dict, idx: int) -> None:
    if m["status"] == "error":
        st.error(m["answer"])
    else:
        st.markdown(m["answer"])
        if m["status"] == "refused":
            st.caption("The assistant did not answer this question.")
        if m["status"] == "needs_confirmation":
            st.info("A draft was saved. Nothing has been submitted: confirm below.")
    for rid in m["draft_ids"]:
        draft_card(rid, "%d_%d" % (idx, rid))
    if m["citations"]:
        with st.expander("Sources (%d)" % len(m["citations"])):
            for c in m["citations"]:
                st.markdown("**%s**%s  \n> %s" % (c["doc_id"], " - " + c["section"] if c["section"] else "", c["quote"]))
                st.caption("passage id: %s" % c["chunk_id"])
    with st.expander("How the agent got this answer"):
        for s in m["steps"]:
            st.text(s["note"])
        for t in m["tool_calls"]:
            st.text("%s(%s)%s" % (t["name"], t["args"], "  ERROR " + t["error"] if t["error"] else ""))
        st.caption("tokens: %d in, %d out%s" % (m["tokens_in"], m["tokens_out"],
                                                 "  |  trace %s" % m["trace_id"] if m["trace_id"] else ""))


def ask(question: str) -> dict:
    before = set(drafts_of(who))
    try:
        resp = agent.run(AgentRequest(question=question, session_id=st.session_state["session_id"], employee_id=who))
    except Exception as exc:  # noqa: BLE001 - the page must not crash on an agent bug
        return {"role": "assistant", "answer": f"Something went wrong ({type(exc).__name__}). Please try again.",
                "status": "error", "citations": [], "steps": [], "tool_calls": [], "tokens_in": 0, "tokens_out": 0,
                "trace_id": "", "draft_ids": sorted(set(drafts_of(who)) - before)}
    return {"role": "assistant", "answer": resp.answer, "status": resp.status,
            "citations": [c.model_dump() for c in resp.citations], "steps": resp.steps, "tool_calls": resp.tool_calls,
            "tokens_in": resp.usage.tokens_in, "tokens_out": resp.usage.tokens_out,
            "trace_id": tracer.last_trace_id if record else "",
            "draft_ids": sorted(set(drafts_of(who)) - before)}  # drafts this turn created (the card finds them by id)


# --- page --------------------------------------------------------------------
st.title("HR Policy Navigator")
if "flash" in st.session_state:
    kind, text = st.session_state.pop("flash")
    getattr(st, kind)(text)
st.caption("Ask about leave policy or your own leave. Answers cite the policy text; the assistant cannot approve "
           "or submit anything.")

question = st.chat_input("Ask a question, for example: how many casual leave days do I have left?", key="question")
if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.spinner("Thinking (this takes 5 to 15 seconds)..."):
        st.session_state["messages"].append(ask(question))

for i, m in enumerate(st.session_state["messages"]):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            show_assistant(m, i)
