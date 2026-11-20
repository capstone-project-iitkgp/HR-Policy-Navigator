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


@dataclass
class Agent:
    tools: ToolBox
    store: SessionStore | None = None
    critic: Literal["off", "advisory", "revise"] = "advisory"  # decision 2
    strict_citations: bool = True  # decision 3
    temperature: float = 0.2
    _seed: int | None = None

    # ---- public ---------------------------------------------------------
    def run(self, request: AgentRequest, seed: int | None = None) -> AgentResponse:
        return self.run_with_state(request, seed)[0]

    def run_with_state(self, request: AgentRequest, seed: int | None = None) -> tuple[AgentResponse, AgentState]:
        who = request.employee_id or ""
        memory = self.store.memory_text(request.session_id, who) if self.store else ""
        state = AgentState(question=memory + request.question, employee_id=who,
                           profile=self.tools.profile(who) if who else {})
        self._seed = seed
        try:
            final = self._pipeline(state)
        except (http.LLMHTTPError, httpx.TransportError) as exc:
            state.note(f"stopped: {type(exc).__name__}")
            return self._response(state, None, BUSY_MESSAGE, "error"), state
        response = self._response(state, final, final.answer, None)
        if self.store and response.status != "error":
            self.store.add_turn(request.session_id, who, request.question, response.answer)
        return response, state

    # ---- stages ---------------------------------------------------------
    def _pipeline(self, state: AgentState) -> FinalAnswer | None:
        state.plan = self._plan(state)
        route = state.plan.route
        if route == "refuse_injection":
            self._search_once(state, state.plan.steps[0] if state.plan.steps else state.question)
        elif route in TOOL_ROUTES:
            self._execute(state, route)
        elif route == "escalate":
            state.note("route escalate: escalate_to_hr is not built yet (H6); the answer says who to contact")
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
        state.step(f"tool {POLICY_TOOL} (once, for a refusal)")
        self._run_tool(state, POLICY_TOOL, {"query": query}, "policy")

    def _run_tool(self, state: AgentState, name: str, args: dict[str, Any], route: str):
        out = self.tools.run(name, args, state.employee_id, route=route)
        state.record_tool(name, args, out.result, ok=bool(out.result.get("ok")))
        state.add_passages(out.passages)
        return out

    def _execute(self, state: AgentState, route: str) -> None:
        schemas = self.tools.schemas(route)
        text = prompts.load("executor").render(plan=state.plan.model_dump_json(), gathered=state.gathered_text())
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
        first, nudged, repeats, bad_turns = True, False, 0, 0
        while True:
            if state.at_cap:
                state.note("step_cap reached: going to the answer with what was gathered")
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
            else:
                result = self._run_tool(state, name, args, route).result
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
        hrms = [r.result for r in state.tool_records if r.ok and not r.blocked and r.name != POLICY_TOOL]
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

    def _problems(self, state: AgentState, final: FinalAnswer, sources: str) -> list[str]:
        problems = render.check_citations(final, state.passages)
        problems += [f"the number {n} is not in the passages, tool results or question"
                     for n in render.unsupported_numbers(final.answer, sources, state.question)]
        return problems

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
        problems = self._problems(state, final, sources)
        if problems and not state.at_cap:
            final = self._revise(state, final, messages, problems, "checks")
            problems = self._problems(state, final, sources)
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
        if self.strict_citations and final.behaviour == "answer" and not final.citations:
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
            if final.behaviour == "draft_for_confirmation" or any(
                    r.name == "create_leave_draft" and r.ok and not r.blocked for r in state.tool_records):
                status = "needs_confirmation"
        return AgentResponse(
            answer=answer, citations=citations,
            steps=[{"note": line} for line in state.trace],
            tool_calls=[{"name": r.name, "args": r.args, "error": None if r.ok else (r.result or {}).get("error")}
                        for r in state.tool_records],
            status=status, usage=state.usage)
