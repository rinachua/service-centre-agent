FROM python:3.11-slim
WORKDIR /app
# sqlite3 CLI is not part of the base image; installed for ad hoc DB
# inspection (docker compose exec equipment-history-service sqlite3 ...).
# Not required by the app itself, which uses Python's stdlib sqlite3 module.
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
