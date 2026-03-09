# Build context: project root (so we can copy shared/ and schemas/)
# Only the minimal Kafka + Avro dependencies are installed — no DB or UI deps.
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY shared/ shared/
COPY schemas/ schemas/
COPY data_quality/ data_quality/

ENV PYTHONPATH=/app

CMD ["python", "data_quality/validation_service.py"]
