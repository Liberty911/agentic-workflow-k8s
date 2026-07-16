"""
main.py — FastAPI entrypoint for the agent orchestrator.

Phase 3 change: the orchestrator no longer runs the LangGraph agent
directly. It is now a thin API layer that:
  1. Accepts a request
  2. Pushes it onto the Redis task queue
  3. Returns a task_id immediately (does not block waiting for the answer)

A separate worker process (built next) consumes the queue and does the
actual agent reasoning. This split is what makes KEDA autoscaling
meaningful later: the orchestrator stays a single lightweight pod, while
worker replicas scale 0→N based on how many tasks are waiting.

Run locally with:
    uvicorn main:app --reload --port 8000

Test with:
    curl -X POST http://localhost:8000/invoke \
      -H "Content-Type: application/json" \
      -d '{"message": "What is the status of order ORD-1001?"}'
    # -> {"task_id": "...", "status": "queued"}

    curl http://localhost:8000/result/<task_id>
    # -> {"status": "pending"}  (until a worker picks it up)
"""

import logging
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from queue_client import TaskQueue

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

app = FastAPI(
    title="Agentic Workflow Orchestrator",
    description="Thin API layer — enqueues tasks onto Redis for worker pods to process",
)

queue = TaskQueue()


class InvokeRequest(BaseModel):
    message: str


class InvokeResponse(BaseModel):
    task_id: str
    status: str


class ResultResponse(BaseModel):
    status: str
    response: Optional[str] = None
    tool_calls_made: Optional[int] = None
    error: Optional[str] = None


@app.get("/health")
def health():
    try:
        queue.client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis_connected": redis_ok}


@app.get("/queue-depth")
def queue_depth():
    """Exposes current queue length — useful for manually watching what
    KEDA will later watch automatically."""
    return {"queue_depth": queue.queue_depth()}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    try:
        task_id = queue.enqueue(req.message)
        logger.info(f"Enqueued task {task_id}")
        return InvokeResponse(task_id=task_id, status="queued")
    except Exception as e:
        logger.exception("Failed to enqueue task")
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {e}")


@app.get("/result/{task_id}", response_model=ResultResponse)
def get_result(task_id: str):
    result = queue.get_result(task_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown task_id, or result expired (TTL 1 hour)",
        )
    return ResultResponse(**result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
