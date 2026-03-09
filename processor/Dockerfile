# Build context: project root (so we can copy shared/, schemas/, data_quality/)
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY schemas/ schemas/
COPY data_quality/ data_quality/

COPY processor/ processor/

ENV PYTHONPATH=/app

CMD ["python", "processor/risk_score_processor.py"]
