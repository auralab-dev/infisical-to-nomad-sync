FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --create-home --uid 10001 app

COPY --chmod=0555 infisical_to_nomad_sync.py ./

USER app

ENTRYPOINT ["python", "/app/infisical_to_nomad_sync.py"]
