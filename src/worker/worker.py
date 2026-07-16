"""
worker.py — Consumes tasks from the Redis queue and runs the LangGraph agent.

This is the piece KEDA will eventually scale 0→N based on Redis queue depth.
For now it runs as a fixed-replica (1) long-lived Deployment: a continuous
loop that blocks on BRPOP, processes one task at a time, and writes the
result back to Redis for the orchestrator's /result endpoint to serve.

Graceful shutdown: on SIGTERM (which Kubernetes sends before killing a pod),
the worker finishes whatever task it's currently on, then exits cleanly
rather than dropping work mid-task.
"""

import logging
import signal
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from graph import build_graph
from queue_client import TaskQueue

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
)
logger = logging.getLogger("worker")

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("Received shutdown signal — finishing current task, then exiting.")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def process_task(app_graph, queue: TaskQueue, task: dict) -> None:
    task_id = task["task_id"]
    message = task["message"]
    logger.info(f"Processing task {task_id}: {message!r}")

    try:
        result = app_graph.invoke(
            {"messages": [HumanMessage(content=message)], "tool_call_count": 0},
            config={"recursion_limit": 15},
        )
        messages = result["messages"]
        final_message = messages[-1]
        tool_call_count = sum(1 for m in messages if getattr(m, "tool_calls", None))

        queue.set_result(
            task_id,
            status="completed",
            response=final_message.content,
            tool_calls_made=tool_call_count,
        )
        logger.info(f"Task {task_id} completed ({tool_call_count} tool call(s))")

    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        queue.set_result(task_id, status="error", error=str(e))


def main():
    logger.info("Building LangGraph agent...")
    app_graph = build_graph()
    logger.info("Agent ready. Starting consume loop.")

    queue = TaskQueue()

    while not _shutdown:
        # 5s timeout means we re-check _shutdown every 5s even when idle,
        # instead of blocking forever on an empty queue.
        task = queue.dequeue(timeout=5)
        if task is None:
            continue
        process_task(app_graph, queue, task)

    logger.info("Worker exiting cleanly.")
    sys.exit(0)


if __name__ == "__main__":
    main()
