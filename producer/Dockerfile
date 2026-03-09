# Build context: project root (so we can copy shared/, schemas/, data_quality/)
# docker compose build context must be set to "." in docker-compose.yml
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared modules needed by this service
COPY shared/ shared/
COPY schemas/ schemas/
COPY data_quality/ data_quality/

# Service code
COPY producer/ producer/

ENV PYTHONPATH=/app

CMD ["python", "producer/transaction_streamer.py"]
