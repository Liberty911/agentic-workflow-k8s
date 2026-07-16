"""
main.py — FastAPI entrypoint for the agent orchestrator.

Phase 1: standalone HTTP API, no Redis/queue yet. Run locally with:
    uvicorn main:app --reload --port 8000

Then test with:
    curl -X POST http://localhost:8000/invoke \
      -H "Content-Type: application/json" \
      -d '{"message": "What is the status of order ORD-1001?"}'
"""

import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from graph import build_graph

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

# Compiled graph is built once at startup, reused across requests
_app_graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_graph
    logger.info("Building LangGraph agent...")
    _app_graph = build_graph()
    logger.info("Agent ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Agentic Workflow Orchestrator",
    description="LangGraph-based agent orchestrator — Phase 1 (standalone, no queue)",
    lifespan=lifespan,
)


class InvokeRequest(BaseModel):
    message: str


class InvokeResponse(BaseModel):
    response: str
    tool_calls_made: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    try:
        result = _app_graph.invoke(
            {"messages": [HumanMessage(content=req.message)], "tool_call_count": 0},
            config={"recursion_limit": 15},
        )
        messages = result["messages"]
        final_message = messages[-1]

        # Count how many tool calls happened in this run
        tool_call_count = sum(
            1 for m in messages
            if getattr(m, "tool_calls", None)
        )

        return InvokeResponse(
            response=final_message.content,
            tool_calls_made=tool_call_count,
        )
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
