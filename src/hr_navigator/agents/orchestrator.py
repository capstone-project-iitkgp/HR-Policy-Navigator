"""The agent loop: plan -> execute (tools) -> answer -> critic, as plain code.

    agent = Agent(ToolBox(search_fn), store=SessionStore())
    response = agent.run(AgentRequest(question=..., session_id=..., employee_id=...))

Each stage is a function below; control flow is ordinary if/while so a failure can be read
in the trace. Every model call goes through ``chat`` (replaced in tests). ``run`` never
raises: capacity and network problems become ``status="error"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from hr_navigator import prompts
from hr_navigator.agents.budget import CONTEXT_LIMIT, OUTPUT_RESERVE, estimate_tokens, fit_context
from hr_navigator.agents.state import AgentState
from hr_navigator.agents.toolbox import POLICY_TOOL, ToolBox
from hr_navigator.agents.validate import BLOCK_ANSWERS, BLOCK_BEHAVIOUR, validate_response
from hr_navigator.llm import client, http
from hr_navigator.prompts import render
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import AgentRequest, AgentResponse, Citation
from hr_navigator.state.session_store import SessionStore

chat = client.chat  # tests replace this
SYSTEM = "You are a careful assistant. Follow the instructions exactly."
TOOL_ROUTES = ("policy", "hrms", "policy_and_hrms")
BUSY_MESSAGE = "The assistant is busy or unreachable right now. Please try again in a minute."
FAILED_MESSAGE = "I could not produce a reliable answer. Please ask HR or try again."
UNVERIFIED_MESSAGE = ("I could not verify an answer against the policy documents, so I am not giving one. "
                      "Please check with HR.")
MAX_BAD_TOOL_TURNS = 2  # consecutive failed tool calls before the loop is left
MAX_REPEATS = 2  # repeated calls (the first is blocked, the second ends the loop)
# behaviours after which code hands the question to HR (escalate_to_hr); the model is never offered that tool
ESCALATE_ON = ("escalate_to_human", "refuse_pii", "refuse_injection", "refuse_not_in_corpus")


@dataclass
class Agent:
    tools: ToolBox
    store: SessionStore | None = None
    critic: Literal["off", "advisory", "revise"] = "advisory"  # decision 2
    strict_citations: bool = True  # decision 3
    block_unsafe: bool = True  # validate_response: replace unsafe answers (False = note only, for ablation)
    temperature: float = 0.2
    raise_http_errors: bool = False  # eval runs: stop on capacity/network errors instead of answering "error"
    _seed: int | None = None

    # ---- public ---------------------------------------------------------
    def run(self, request: AgentRequest, seed: int | None = None) -> AgentResponse:
        return self.run_with_state(request, seed)[0]

    def run_with_state(self, request: AgentRequest, seed: int | None = None, *,
                       profile: dict[str, Any] | None = None, rag: bool = False) -> tuple[AgentResponse, AgentState]:
        """``profile`` replaces the HR-system profile (eval items carry their own). ``rag=True`` is the
        v2 baseline: one search by code, then the answer stage, no planner and no tool loop."""
        who = request.employee_id or ""
        memory = self.store.memory_text(request.session_id, who) if self.store else ""
        state = AgentState(question=memory + request.question, employee_id=who,
                           profile=profile if profile is not None else (self.tools.profile(who) if who else {}))
        self._seed = seed
        try:
            final = self._rag_pipeline(state) if rag else self._pipeline(state)
        except (http.LLMHTTPError, httpx.TransportError) as exc:
            if self.raise_http_errors:
                raise
            state.note(f"stopped: {type(exc).__name__}")
            return self._response(state, None, BUSY_MESSAGE, "error"), state
        if final is not None and final.behaviour in ESCALATE_ON and who:
            final = self._escalate(state, final, request.question)
        state.final_behaviour = final.behaviour if final else ""
        response = self._response(state, final, final.answer if final else "", None)
        if self.store and response.status != "error":
            self.store.add_turn(request.session_id, who, request.question, response.answer)
        return response, state

    # ---- stages ---------------------------------------------------------
    def _rag_pipeline(self, state: AgentState) -> FinalAnswer | None:
        state.plan = Plan(route="policy", reason="single retrieval by code", steps=[state.question[:100]])
        state.step(f"tool {POLICY_TOOL} (by code)")
        self._run_tool(state, POLICY_TOOL, {"query": state.question[:300]}, "policy")
        return self._answer_and_check(state)

    def _pipeline(self, state: AgentState) -> FinalAnswer | None:
        state.plan = self._plan(state)
        route = state.plan.route
        if route in ("refuse_injection", "clarify"):  # clarify needs the policy to name the options
            self._search_once(state, state.plan.steps[0] if state.plan.steps else state.question)
        elif route in TOOL_ROUTES:
            self._execute(state, route)
        elif route == "escalate":
            state.note("route escalate: no tools; the hand-over to HR is made by code after the answer")
        return self._answer_and_check(state)

    def _llm(self, state: AgentState, messages: list[dict], tag: str, *, schema=None, tools=None,
             tool_choice=None, max_tokens: int = 500):
        result = chat(messages, schema=schema, tools=tools, tool_choice=tool_choice, max_tokens=max_tokens,
                      tag=tag, temperature=self.temperature, seed=self._seed)
        state.usage = state.usage + result.usage
        return result

    def _prompt_call(self, state: AgentState, name: str, schema, max_tokens: int = 500, **values):
        text = prompts.load(name).render(**values)
        return self._llm(state, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                         f"agent_{name}", schema=schema, max_tokens=max_tokens).parsed

    def _common(self, state: AgentState) -> dict[str, str]:
        profile = "; ".join(f"{k}: {v}" for k, v in state.profile.items()) or "(unknown)"
        return {"today": str(state.profile.get("today") or "unknown"), "profile": profile, "question": state.question}

    # -- plan
    def _plan(self, state: AgentState) -> Plan:
        state.step("plan")
        try:
            plan = self._prompt_call(state, "planner", Plan, **self._common(state))
        except client.SchemaError as exc:
            state.usage = state.usage + exc.result.usage
            state.note("planner gave invalid JSON twice: fallback plan (route policy, search the question)")
            return Plan(route="policy", reason="fallback", steps=[state.question[:100]])
        state.trace[-1] += f" -> route {plan.route}"
        return plan

    # -- execute
    def _search_once(self, state: AgentState, query: str) -> None:
        state.step(f"tool {POLICY_TOOL} (once, by code)")
        self._run_tool(state, POLICY_TOOL, {"query": query}, "policy")

    def _run_tool(self, state: AgentState, name: str, args: dict[str, Any], route: str):
        out = self.tools.run(name, args, state.employee_id, route=route)
        state.record_tool(name, args, out.result, ok=bool(out.result.get("ok")))
        state.add_passages(out.passages)
        return out

    def _execute(self, state: AgentState, route: str) -> None:
        schemas = self.tools.schemas(route)
        text = prompts.load("executor").render(plan=state.plan.model_dump_json(), gathered=state.gathered_text(),
                                               **self._common(state))
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
        first, nudged, repeats, bad_turns, empty_searches = True, False, 0, 0, 0
        while True:
            if state.at_cap:
                state.note("step_cap reached: going to the answer with what was gathered")
                return
            if not schemas:
                return
            if estimate_tokens(json.dumps(messages) + json.dumps(schemas)) > CONTEXT_LIMIT - OUTPUT_RESERVE:
                state.note("token budget reached in the executor: going to the answer")
                return
            state.step("executor")
            res = self._llm(state, messages, "agent_executor", tools=schemas,
                            tool_choice="required" if first else "auto", max_tokens=300)
            first = False
            if not res.tool_calls:
                if "DONE" in res.text.upper() or nudged:
                    return
                nudged = True
                state.note("executor gave no tool call and no DONE: nudged once")
                messages += [{"role": "assistant", "content": res.text or "..."},
                             {"role": "user", "content": "Call a tool, or reply DONE if you have enough."}]
                continue
            call = res.tool_calls[0]  # one at a time, as the prompt says
            fn = call.get("function", {})
            name, raw = fn.get("name", ""), fn.get("arguments")
            if name.strip().upper() == "DONE":  # Qwen sometimes "calls" DONE as if it were a tool
                state.step("executor said DONE (as a tool call)")
                return
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
            except ValueError:
                args = {"_raw": str(raw)[:200]}
            args = args if isinstance(args, dict) else {"_raw": str(args)[:200]}
            messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
            state.step(f"tool {name}")
            if state.is_repeat(name, args):
                repeats += 1
                result = {"ok": False, "error": "repeated_call", "message": "You already made this call. Change it or reply DONE."}
                state.record_tool(name, args, result, ok=False, blocked=True)
                state.note("repeated call blocked")
                if repeats >= MAX_REPEATS:
                    state.note("repeated again: leaving the loop")
                    return
            elif name not in [t["function"]["name"] for t in schemas]:
                result = {"ok": False, "error": "tool_not_available", "message": f"{name!r} is not available now."}
                state.record_tool(name, args, result, ok=False, blocked=True)
                bad_turns += 1
            elif name == "cancel_leave_request" and not any(
                    r.name == "list_my_requests" and r.ok for r in state.tool_records):
                result = {"ok": False, "error": "list_first",
                          "message": "Never guess a request id. Call list_my_requests first and use an id from it."}
                state.record_tool(name, args, result, ok=False, blocked=True)
                state.note("cancel before list_my_requests blocked")
                bad_turns += 1
            else:
                before = len(state.passages)
                out = self._run_tool(state, name, args, route)
                result = out.result
                if name == POLICY_TOOL and result.get("ok"):
                    empty_searches = empty_searches + 1 if not state.passages else 0
                    if (before and len(state.passages) == before) or empty_searches >= 2:
                        state.note("search found nothing new: policy_search is no longer offered")
                        schemas = [t for t in schemas if t["function"]["name"] != POLICY_TOOL]
                        result = dict(result, note="policy_search is no longer available. Use the other tools, or reply DONE.")
                else:
                    bad_turns = 0 if result.get("ok") else bad_turns + 1
            if bad_turns >= MAX_BAD_TOOL_TURNS:
                state.note("two failed tool calls in a row: leaving the loop")
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(result)})
                return
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)})

    # -- answer
    def _answer_prompt(self, state: AgentState) -> tuple[str, str, str]:
        """Render the answer prompt with as much material as fits. Returns (prompt, passages_text, tools_text)."""
        hrms = [f"{r.name}({json.dumps(r.args, ensure_ascii=False)}) -> {json.dumps(r.result, ensure_ascii=False)}"
                for r in state.tool_records if not r.blocked and r.name != POLICY_TOOL]
        values = dict(self._common(state), plan=state.plan.model_dump_json())
        template = prompts.load("answer")
        fixed = template.render(**values, passages="", tool_results="")
        shown, tool_texts, notes = fit_context(fixed, state.passages, hrms)
        for n in notes:
            state.note(n)
        passages_text = render.format_passages(shown)
        tools_text = "\n".join(tool_texts) or "(none)"
        return template.render(**values, passages=passages_text, tool_results=tools_text), passages_text, tools_text

    def _answer_and_check(self, state: AgentState) -> FinalAnswer | None:
        prompt, passages_text, tools_text = self._answer_prompt(state)
        sources = passages_text + "\n" + tools_text
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        state.note("answer")
        try:
            final = self._llm(state, messages, "agent_answer", schema=FinalAnswer, max_tokens=600).parsed
        except client.SchemaError as exc:
            state.usage = state.usage + exc.result.usage
            state.note(f"answer was invalid JSON twice; raw text: {exc.result.text[:200]!r}")
            return None
        final = self._check_and_revise(state, final, messages, sources)
        final = self._run_critic(state, final, messages, passages_text, tools_text)
        return final

    @staticmethod
    def _needs_citation(state: AgentState) -> bool:
        """An answer built only from the employee's own HR data (no passages) has nothing to cite."""
        from_hrms = any(not r.blocked and r.name != POLICY_TOOL for r in state.tool_records)
        return not (from_hrms and not state.passages)

    def _validate(self, state: AgentState, final: FinalAnswer, sources: str):
        """Run validate_response (code, not a model call) and keep a record of it."""
        v = validate_response(final, question=state.question, passages=state.passages, tool_records=state.tool_records,
                              actor_id=state.employee_id, sources=sources, needs_citation=self._needs_citation(state))
        state.record_code_tool("validate_response", {}, {"ok": True, "issues": [i.code for i in v.issues]})
        return v

    def _assess(self, state: AgentState, final: FinalAnswer, sources: str) -> tuple[FinalAnswer | None, list[str]]:
        """(replacement, fixable problems). A replacement is set only for a safety block."""
        v = self._validate(state, final, sources)
        if v.blocks:
            first = v.blocks[0]
            state.note(f"validate_response: blocked ({first.code}): {first.detail}"
                       + ("" if self.block_unsafe else " [report only]"))
            if self.block_unsafe:
                return FinalAnswer(behaviour=BLOCK_BEHAVIOUR[first.code], answer=BLOCK_ANSWERS[first.code]), []
        return None, [i.detail for i in v.fixes]

    def _escalate(self, state: AgentState, final: FinalAnswer, question: str) -> FinalAnswer:
        """Hand the question to HR after a refusal or escalation: an audit-log note plus the contact."""
        args = {"topic": final.behaviour, "summary": question[:200]}
        out = self.tools.escalate(state.employee_id, **args)
        state.record_code_tool("escalate_to_hr", args, out, ok=bool(out.get("ok")))
        if not out.get("ok"):
            state.note(f"escalate_to_hr failed: {out.get('error')}")
            return final
        state.note(f"escalate_to_hr: {out['reference']}")
        return final.model_copy(update={"answer": f"{final.answer}\n\nI have noted this for HR (reference "
                                                  f"{out['reference']}). Contact: {out['contact']}."})

    def _revise(self, state: AgentState, final: FinalAnswer, messages: list[dict], issues: list[str], tag: str):
        state.step(f"revision ({tag})")
        follow = messages + [{"role": "assistant", "content": final.model_dump_json()},
                             {"role": "user", "content": "Fix these problems and reply with the JSON object only:\n- "
                              + "\n- ".join(issues)}]
        try:
            return self._llm(state, follow, "agent_revise", schema=FinalAnswer, max_tokens=600).parsed
        except client.SchemaError as exc:
            state.usage = state.usage + exc.result.usage
            state.note("revision was invalid JSON: kept the first answer")
            return final

    def _check_and_revise(self, state, final, messages, sources) -> FinalAnswer:
        if not self._needs_citation(state) and final.citations:  # HR data has no passage to cite
            state.note("answer is from HR data only: citations dropped")
            final = final.model_copy(update={"citations": []})
        final, repaired = render.snap_quotes(final, state.passages)
        if repaired:
            state.note(f"{repaired} near-verbatim quote(s) replaced by the passage's own sentence")
        blocked, problems = self._assess(state, final, sources)
        if blocked:
            return blocked
        if problems and not state.at_cap:
            final = self._revise(state, final, messages, problems, "checks")
            blocked, problems = self._assess(state, final, sources)
            if blocked:
                return blocked
        if not problems:
            return final
        state.note("still failing after revision: " + "; ".join(problems)[:300])
        cites = render.check_citations  # keep only citations that verify
        good = [c for c in final.citations
                if not cites(FinalAnswer(behaviour="refuse_not_in_corpus", answer="", citations=[c]), state.passages)]
        dropped = len(final.citations) - len(good)
        if dropped and self.strict_citations:
            final = final.model_copy(update={"citations": good})
            state.note(f"dropped {dropped} citation(s) that did not verify")
        if self.strict_citations and final.behaviour == "answer" and not final.citations and self._needs_citation(state):
            state.note("no verified citation left: downgraded to refuse_not_in_corpus")
            return FinalAnswer(behaviour="refuse_not_in_corpus", answer=UNVERIFIED_MESSAGE)
        return final  # an unsupported number alone is noted, not blocked (the check has false positives)

    # -- critic
    def _run_critic(self, state, final, messages, passages_text, tools_text) -> FinalAnswer:
        if self.critic == "off" or state.at_cap:
            return final
        state.step("critic")
        try:
            verdict = self._prompt_call(state, "critic", Critique, behaviour=final.behaviour,
                                        profile=self._common(state)["profile"], question=state.question,
                                        passages=passages_text, tool_results=tools_text, draft=final.answer)
        except client.SchemaError as exc:
            state.usage = state.usage + exc.result.usage
            state.note("critic gave invalid JSON: ignored")
            return final
        state.trace[-1] += f" -> {verdict.verdict}" + (f": {'; '.join(verdict.issues)}"[:200] if verdict.issues else "")
        if self.critic == "revise" and verdict.verdict == "revise" and not state.at_cap:
            return self._revise(state, final, messages, verdict.issues or ["the critic asked for a revision"], "critic")
        return final

    # ---- result ---------------------------------------------------------
    def _response(self, state: AgentState, final: FinalAnswer | None, answer: str, forced: str | None) -> AgentResponse:
        status: str = forced or "answered"
        citations: list[Citation] = []
        if final is None and forced is None:
            answer, status = FAILED_MESSAGE, "error"
        elif final is not None:
            by_id = {p.chunk_id: p for p in state.passages}
            citations = [Citation(doc_id=by_id[c.passage_id].doc_id, chunk_id=c.passage_id,
                                  section=by_id[c.passage_id].title, quote=c.quote)
                         for c in final.citations if c.passage_id in by_id]
            if final.behaviour in ("refuse_injection", "refuse_pii", "refuse_not_in_corpus"):
                status = "refused"
            done = lambda n: any(r.name == n and r.ok and not r.blocked for r in state.tool_records)  # noqa: E731
            cancelled_only = done("cancel_leave_request") and not done("create_leave_draft")
            if (final.behaviour == "draft_for_confirmation" and not cancelled_only) or done("create_leave_draft"):
                status = "needs_confirmation"
        return AgentResponse(
            answer=answer, citations=citations,
            steps=[{"note": line} for line in state.trace],
            tool_calls=[{"name": r.name, "args": r.args, "error": None if r.ok else (r.result or {}).get("error")}
                        for r in state.tool_records + state.code_tools],
            status=status, usage=state.usage)
