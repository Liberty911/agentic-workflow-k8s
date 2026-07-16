"""
queue_client.py — Redis-backed task queue.

The orchestrator acts as a producer: it enqueues tasks and returns
immediately. Workers (built in the next phase) act as consumers: they
block-pop tasks from the queue, run the LangGraph agent, and write the
result back under a per-task result key.

Queue design:
  - agent:tasks            → Redis LIST, used as the work queue (LPUSH/BRPOP)
  - agent:result:<task_id> → Redis STRING (JSON), holds status + result,
                              expires after RESULT_TTL_SECONDS so completed
                              tasks don't accumulate forever
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis

QUEUE_KEY = "agent:tasks"
RESULT_KEY_PREFIX = "agent:result:"
RESULT_TTL_SECONDS = 3600  # 1 hour


def get_redis_client() -> redis.Redis:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


class TaskQueue:
    def __init__(self, client: Optional[redis.Redis] = None):
        self.client = client or get_redis_client()

    def enqueue(self, message: str) -> str:
        """Push a new task onto the queue. Returns the generated task_id."""
        task_id = str(uuid.uuid4())

        # Set initial pending status before pushing to the queue, so a
        # client polling immediately after enqueue never hits a 404.
        self._set_result(task_id, {
            "status": "pending",
            "response": None,
            "tool_calls_made": None,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        })

        payload = json.dumps({"task_id": task_id, "message": message})
        self.client.lpush(QUEUE_KEY, payload)

        return task_id

    def dequeue(self, timeout: int = 5) -> Optional[dict]:
        """
        Blocking pop from the queue. Used by workers.
        Returns None if no task arrived within `timeout` seconds.
        """
        item = self.client.brpop(QUEUE_KEY, timeout=timeout)
        if item is None:
            return None
        _, payload = item
        return json.loads(payload)

    def queue_depth(self) -> int:
        """Number of tasks currently waiting. This is what KEDA will watch."""
        return self.client.llen(QUEUE_KEY)

    def get_result(self, task_id: str) -> Optional[dict]:
        raw = self.client.get(f"{RESULT_KEY_PREFIX}{task_id}")
        if raw is None:
            return None
        return json.loads(raw)

    def set_result(self, task_id: str, status: str, response: str = None,
                    tool_calls_made: int = None, error: str = None) -> None:
        """Used by workers to write back the completed (or failed) result."""
        self._set_result(task_id, {
            "status": status,
            "response": response,
            "tool_calls_made": tool_calls_made,
            "error": error,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _set_result(self, task_id: str, data: dict) -> None:
        self.client.setex(
            f"{RESULT_KEY_PREFIX}{task_id}",
            RESULT_TTL_SECONDS,
            json.dumps(data),
        )
