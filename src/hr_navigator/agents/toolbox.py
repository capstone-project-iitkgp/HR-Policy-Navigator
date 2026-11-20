"""The tools the executor may call, behind one interface.

    box = ToolBox(search_fn)
    box.schemas("policy_and_hrms")          -> OpenAI tool definitions to offer for a route
    box.run("policy_search", {...}, "D010", route="policy") -> ToolResult

``run`` never raises. Problems come back as {"ok": False, "error": ..., "message": ...}
so the model can correct itself. The search function is injected (Akash's ``search`` once it
exists; a fixture in tests) so this file does not depend on retrieval.

Not here (H6): escalate_to_hr, validate_response, audit checks.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, NamedTuple

from hr_navigator.schemas import Passage
from hrms import agent_tools as hrms_tools

log = logging.getLogger(__name__)

SearchFn = Callable[..., list[Passage]]  # search(query, k=5, filters=None)

POLICY_TOOL = "policy_search"
# get_my_profile is read by the orchestrator in code, so the model is not offered it.
HRMS_TOOLS = tuple(n for n in hrms_tools.TOOLS if n != "get_my_profile")
ROUTE_TOOLS: dict[str, tuple[str, ...]] = {
    "policy": (POLICY_TOOL,),
    "hrms": HRMS_TOOLS,
    "policy_and_hrms": (POLICY_TOOL, *HRMS_TOOLS),
}
IDENTITY_ARGS = ("employee_id", "emp_id", "actor_id", "actor", "user_id")  # never taken from the model
SEARCH_K = 5
SNIPPET_CHARS = 300  # what the model sees of each hit; the answer step gets the full passage
MAX_QUERY_CHARS = 300

POLICY_SCHEMA = {"type": "function", "function": {
    "name": POLICY_TOOL,
    "description": "Search the company policy documents. Give a short phrase with the key terms, "
                   "for example 'casual leave days per year'. Returns the best matching passages.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Key terms to look for"}},
        "required": ["query"], "additionalProperties": False}}}


class ToolResult(NamedTuple):
    result: dict[str, Any]  # what the model is shown
    passages: list[Passage]  # full passages found (policy_search only); the orchestrator keeps them


def _error(code: str, message: str) -> ToolResult:
    return ToolResult({"ok": False, "error": code, "message": message}, [])


class ToolBox:
    def __init__(self, search_fn: SearchFn):
        self._search = search_fn

    def schemas(self, route: str) -> list[dict[str, Any]]:
        names = ROUTE_TOOLS.get(route, ())
        hrms = {s["function"]["name"]: s for s in hrms_tools.tool_schemas()}
        return [POLICY_SCHEMA if n == POLICY_TOOL else hrms[n] for n in names]

    def profile(self, actor_id: str) -> dict[str, Any]:
        """The employee's profile, read by code. {} if the HR system cannot give it."""
        result = hrms_tools.run_tool(actor_id, "get_my_profile")
        return {k: v for k, v in result.items() if k != "ok"} if result.get("ok") else {}

    def run(self, name: str, arguments: Any, actor_id: str, *, route: str | None = None) -> ToolResult:
        """Run one tool as ``actor_id``. With ``route``, only tools offered for it are allowed."""
        if route is not None and name not in ROUTE_TOOLS.get(route, ()):
            return _error("tool_not_available", f"{name!r} is not available for this question.")
        args = self._parse(arguments)
        if args is None:
            return _error("bad_arguments", 'The arguments must be one JSON object, for example {"query": "..."}.')
        for key in IDENTITY_ARGS:  # decision 7: the employee comes from the request, never the model
            args.pop(key, None)
        if name == POLICY_TOOL:
            return self._policy_search(args)
        return ToolResult(hrms_tools.run_tool(actor_id, name, args), [])

    @staticmethod
    def _parse(arguments: Any) -> dict[str, Any] | None:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return None
        return dict(arguments) if isinstance(arguments, dict) else None

    def _policy_search(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query")
        if set(args) - {"query"} or not isinstance(query, str) or not query.strip():
            return _error("bad_arguments", "policy_search needs exactly one argument, a non-empty 'query'.")
        query = query.strip()[:MAX_QUERY_CHARS]
        try:
            found = list(self._search(query, k=SEARCH_K))
        except Exception:  # retrieval is another module; whatever it raises, the loop must go on
            log.exception("policy_search failed")
            return _error("search_failed", "The policy search failed. Try again or answer with what you have.")
        hits = [{"id": p.chunk_id, "title": p.title or p.doc_id, "text": p.text[:SNIPPET_CHARS]} for p in found]
        result: dict[str, Any] = {"ok": True, "count": len(hits), "passages": hits}
        if not hits:
            result["message"] = "No passage matched. Change the query once, then stop."
        return ToolResult(result, found)
