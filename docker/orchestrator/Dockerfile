# docker/orchestrator/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal — no compiler toolchain needed for these packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py queue_client.py ./

# Non-root user — good practice, and directly relevant to your
# "rootless containers" portfolio talking point
RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
