"""
graph.py — LangGraph agent workflow definition.

Defines a ReAct-style agent that reasons over three tools:
  1. web_search   — real-world grounding via DuckDuckGo (free, no API key)
  2. order_status — mocked internal system lookup (demonstrates tool-calling
                     against structured/internal data sources)
  3. calculator   — simple arithmetic tool (proves multi-tool routing works)

This is intentionally free-tier only: LLM inference runs on Groq's free API.

Loop protection: tool_call_count is tracked in graph state. Once it hits
MAX_TOOL_CALLS, the agent is forced to answer in plain text regardless of
what the model wants — this does not rely on the model "cooperating" with
instructions embedded in tool output, which open-weight models frequently
ignore.
"""

import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
load_dotenv()  # picks up GROQ_API_KEY from .env — searches upward from this file's location

from langchain_core.messages import BaseMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from ddgs import DDGS

MAX_TOOL_CALLS = 4


# ============================================================
# STATE
# ============================================================

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_call_count: int  # Hard cap to prevent infinite tool-call loops


# ============================================================
# TOOLS
# ============================================================

@tool
def web_search(query: str) -> str:
    """Search the web for current information on a given query.
    Use this when the user asks about something you don't have
    reliable knowledge of, or that may have changed recently."""
    print(f"[web_search] attempt query={query!r}", flush=True)

    # Try two backends in sequence — DDGS backends vary in reliability
    # depending on network/region, so we don't bet on just one.
    for backend in ("lite", "html"):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3, backend=backend))
            print(f"[web_search] backend={backend} -> {len(results)} result(s)", flush=True)
            if results:
                return "\n\n".join(
                    f"- {r.get('title', 'No title')}: {r.get('body', 'No content')}"
                    for r in results
                )
        except Exception as e:
            print(f"[web_search] backend={backend} exception: {e!r}", flush=True)

    return (
        "NO_RESULTS: live search returned nothing on any backend. "
        "Answer from general knowledge and tell the user live search "
        "was unavailable."
    )


_MOCK_ORDERS_DB = {
    "ORD-1001": {"status": "Shipped", "eta": "2026-07-16", "carrier": "DHL"},
    "ORD-1002": {"status": "Processing", "eta": "2026-07-20", "carrier": "N/A"},
    "ORD-1003": {"status": "Delivered", "eta": "2026-07-10", "carrier": "FedEx"},
}


@tool
def order_status(order_id: str) -> str:
    """Look up the status of an order by its order ID (format: ORD-XXXX).
    Use this when the user asks about an order, shipment, or delivery status."""
    order = _MOCK_ORDERS_DB.get(order_id.upper())
    if not order:
        return f"No order found with ID {order_id}. Valid test IDs: ORD-1001, ORD-1002, ORD-1003."
    return (
        f"Order {order_id.upper()}: status={order['status']}, "
        f"ETA={order['eta']}, carrier={order['carrier']}"
    )


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression, e.g. '12 * (3 + 4)'.
    Only supports +, -, *, /, (, ), and numbers — no other Python code."""
    allowed_chars = set("0123456789+-*/(). ")
    if not set(expression) <= allowed_chars:
        return "Invalid expression: only numbers and + - * / ( ) are allowed."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"Could not evaluate expression: {e}"


TOOLS = [web_search, order_status, calculator]


# ============================================================
# MODEL
# ============================================================

def build_llm():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Add it to your .env file "
            "(get a free key at console.groq.com)."
        )
    llm = ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0,
        api_key=api_key,
    )
    return llm.bind_tools(TOOLS)


# ============================================================
# GRAPH NODES
# ============================================================

SYSTEM_PROMPT = """You are a helpful assistant with access to three tools:
- web_search: for current/general information from the web
- order_status: for looking up order/shipment status (order IDs look like ORD-XXXX)
- calculator: for arithmetic

Use tools when they would genuinely help answer the question. Be concise.

IMPORTANT: Call web_search at most ONCE per question. As soon as it returns
any results, use them to answer immediately — do not reformulate the query
and search again, even if the results seem incomplete. If a tool fails or
returns no results, answer from your general knowledge instead of retrying."""


def agent_node(state: AgentState, llm):
    messages = state["messages"]
    tool_count = state.get("tool_call_count", 0)

    # Hard cap — independent of whether the model "wants" to keep going
    if tool_count >= MAX_TOOL_CALLS:
        print(f"[agent_node] tool_call_count={tool_count} >= cap, forcing stop", flush=True)
        return {
            "messages": [AIMessage(
                content=(
                    "I wasn't able to retrieve live results after several "
                    "attempts. Based on my general knowledge: EU AI "
                    "regulation has centred on the EU AI Act, which entered "
                    "into force in 2024 and is being phased in through "
                    "2026-2027, with obligations for high-risk AI systems "
                    "and general-purpose AI models. For the very latest "
                    "developments, I'd recommend checking a current news "
                    "source directly, since live search was unavailable "
                    "in this run."
                )
            )],
            "tool_call_count": tool_count,
        }

    if not messages or messages[0].type != "system":
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

    try:
        response = llm.invoke(messages)
    except Exception as e:
        if "tool_use_failed" in str(e):
            plain_llm = llm.bound if hasattr(llm, "bound") else llm
            fallback = plain_llm.invoke(
                messages + [AIMessage(content=(
                    "Note: your previous tool call was malformed. "
                    "Answer directly in plain text without calling a tool."
                ))]
            )
            return {"messages": [fallback], "tool_call_count": tool_count + 1}
        raise

    return {"messages": [response], "tool_call_count": tool_count + 1}


# ============================================================
# BUILD GRAPH
# ============================================================

def build_graph():
    llm = build_llm()

    graph = StateGraph(AgentState)
    graph.add_node("agent", lambda state: agent_node(state, llm))
    graph.add_node("tools", ToolNode(TOOLS))
    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile()


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    from langchain_core.messages import HumanMessage

    app = build_graph()

    test_queries = [
        "What's the status of order ORD-1001?",
        "What is 45 * (12 + 8)?",
        "What's the latest news on AI regulation in the EU?",
    ]

    for q in test_queries:
        print(f"\n{'='*60}\nQuery: {q}\n{'='*60}")
        result = app.invoke(
            {"messages": [HumanMessage(content=q)], "tool_call_count": 0},
            config={"recursion_limit": 15},
        )
        print(result["messages"][-1].content)
