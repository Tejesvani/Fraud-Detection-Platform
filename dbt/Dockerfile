FROM python:3.9-slim

WORKDIR /app

RUN pip install --no-cache-dir dbt-postgres==1.7.*

COPY dbt_project.yml profiles.yml ./
COPY models/ models/
COPY tests/ tests/
COPY macros/ macros/

# Default: run all models then all tests
CMD ["sh", "-c", "dbt run --profiles-dir . && dbt test --profiles-dir ."]
