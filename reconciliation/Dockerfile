FROM python:3.9-slim

WORKDIR /app

COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt

COPY shared/ shared/
COPY schemas/ schemas/
COPY reconciliation/ reconciliation/

ENV PYTHONPATH=/app

CMD ["python", "reconciliation/reconciliation_job.py"]
