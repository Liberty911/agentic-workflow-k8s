# docker/worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py graph.py queue_client.py ./

RUN useradd -m -u 1000 appuser
USER appuser

# No exposed port — this is a queue consumer, not an HTTP service.
# Kubernetes will restart the pod automatically if the process exits
# with a non-zero code (crash), which is sufficient liveness handling
# for a worker with no HTTP endpoint to probe.
CMD ["python", "worker.py"]
