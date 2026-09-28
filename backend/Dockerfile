# Sauda backend — production image (Python 3.12)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Default: fully self-contained (in-memory stores, mock adapters).
# Set STORAGE_MODE=postgres / REDIS_URL / DATABASE_URL for the prod shape.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
