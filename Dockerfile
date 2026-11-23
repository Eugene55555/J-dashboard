FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ingest ./ingest
RUN mkdir -p /app/data/inbox /app/data/accounts

ENV J_DB=/app/data/dashboard.db \
    J_INBOX=/app/data/inbox \
    J_INBOX_ROOT=/app/data \
    J_ACCOUNTS=/app/data/accounts.json

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
