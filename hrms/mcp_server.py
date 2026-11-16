"""MCP server for the dummy HRMS: the same 8 tools as hrms/agent_tools.py, over stdio.

    HRMS_ACTING_EMPLOYEE=D010 python -m hrms.mcp_server        (from the Code/ folder)

Why it is safe to hand to an agent:
  * The employee the tools act as is fixed when the server starts (HRMS_ACTING_EMPLOYEE), the way
    a login would be. No tool has an employee-id argument, so the model cannot pick someone else.
  * Every call goes through agent_tools.run_tool, so the same argument checks, the same trimmed
    results and the same "agent" audit channel apply as when the tools are called in-process.
  * There are no tools to submit, change an approved request, approve or reject leave.

Uses the low-level MCP server so the tool schemas the model sees are exactly the ones run_tool enforces.
"""

import asyncio
import json
import os
import sys
from contextlib import closing

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import agent_tools, db

ENV_VAR = "HRMS_ACTING_EMPLOYEE"
INSTRUCTIONS = (
    "Tools for one employee's own leave: balances, holidays, manager, requests, policy checks and "
    "leave drafts. They always act as the logged-in employee; never ask for an employee id. "
    "A draft is not filed: the employee must confirm it in the HR app."
)


def build_server(actor_id):
    """An MCP server whose tools all act as `actor_id`."""

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[
            types.Tool(name=s["function"]["name"], description=s["function"]["description"],
                       input_schema=s["function"]["parameters"])
            for s in agent_tools.tool_schemas()])

    async def call_tool(ctx, params):
        result = agent_tools.run_tool(actor_id, params.name, params.arguments)
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result))],
            structured_content=result, is_error=not result["ok"])

    return Server("hrms-leave", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(actor_id):
    server = build_server(actor_id)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    actor_id = os.environ.get(ENV_VAR, "").strip()
    if not actor_id:
        sys.exit("Set %s to the employee the tools should act as, for example D010." % ENV_VAR)
    with closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (actor_id,)).fetchone() is None:
            sys.exit("%s=%s is not an employee in the HRMS database." % (ENV_VAR, actor_id))
    asyncio.run(serve(actor_id))


if __name__ == "__main__":
    main()
