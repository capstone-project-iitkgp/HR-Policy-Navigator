# H8: Streamlit chat page

Status 20 Nov 2026: built, 488 tests pass (headless `AppTest` with a scripted model). **Not yet run on the real endpoint or looked at in a browser.** Policy answers cannot be demonstrated until Akash's `retrieval.search` exists.

## Decisions (Himadri, 20 Nov)
1. The page calls the agent in the same process (`Agent.run`), not Akash's FastAPI `/chat` (A6, not built). One function (`ask` in the page) is the only place to change if the API arrives.
2. A new page, `app/chat_app.py`; `hrms/demo_app.py` (212 tests) is left alone.

## What was built
- **Login:** the same demo login as `hrms/demo_app.py`. The chosen person is passed as `employee_id`; the model cannot change it (checked in the trace: `user.id`). Another person or "New chat" starts a new session and clears the history.
- **Chat:** `st.chat_input` and `st.chat_message`; a spinner during the run. Conversation memory is the H5 `SessionStore` (SQLite under `.cache/`; `SESSION_DB_PATH` overrides). An agent bug becomes an error message, never a crash.
- **Under each answer:** "Sources (n)" (document, section, quote, passage id); "How the agent got this answer" (steps, tool calls with errors, tokens, trace id). Errors show as a red notice; refusals get a caption (and the "noted for HR, reference ESC-n" sentence from H6).
- **Drafts:** after a turn the page compares the employee's DRAFT requests before and after, and shows a card for each new draft: the request, a live policy check, a document checkbox when one is required, and two buttons. **Confirm and submit** is the only place the page calls `submit_request(..., confirmed=True)`; **Discard** cancels the draft. The agent has no such tool, so nothing is filed without a click. The draft is read from the HR service, not from the agent's response, because `AgentResponse.tool_calls` carries no tool results.
- **Traces:** a "Record traces" box (on by default) turns H7 tracing on for the page; the trace id is shown under the answer and can be opened in `app/trace_page.py`.
- **No retrieval yet:** the sidebar warns, and policy questions get no passages (the agent says the documents do not say).
- Tests (`tests/test_chat_app.py`, 15): login and warning, HR answer and steps, sources, model error then recovery, two turns, persona and New chat, acting employee and trace, traces off, refusal with ESC reference, memory across turns, and five draft tests (no filing before the click, confirm, discard, missing document, no buttons without a draft).

## Deviations from the plan
- No sidebar list of older drafts: the card appears only for drafts created in the chat. Older drafts (for example D010's seeded 1 Dec draft) are handled on the HRMS page.
- Chat history lives in `st.session_state` only; closing the browser tab loses it (the SQLite memory keeps the last turns for the model only).

## Known limits
- The Confirm button needs the HR database and the agent to run in the same process. A split deployment (API plus UI) would need an endpoint for it.
- UNVERIFIED: how it looks and feels in a browser, and the real response time (5 to 15 s per question was seen in earlier runs).
- Cached model answers (`.cache/llm`) make repeated questions look instant; move the folder aside for a realistic demo timing.

## To run (Himadri, Zscaler off)
```
cd Code
read -s API_KEY; export API_KEY
export CHAT_BASE_URL=https://103.42.50.41:1033/v1 CHAT_MODEL=Qwen/Qwen3-8B CAPSTONE_CA_CERT=$PWD/../02_Team_HR_Policy_Navigator/certs/vm05-ca.crt
mv .cache/llm .cache/llm_keep          # real timings; restore afterwards: mv .cache/llm .cache/llm_new && mv .cache/llm_keep .cache/llm
.venv/bin/streamlit run app/chat_app.py
```
As D010: "How many casual leave days do I have left?"; then "Apply for casual leave on 2 December 2026" and click Confirm; then, as another person, a question about someone else's salary (refusal with an ESC reference). Open `app/trace_page.py` for the trace.
