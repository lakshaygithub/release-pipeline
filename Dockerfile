FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Baked into the image so every release reports which build it is.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
# Only set to true when building a deliberately broken image to test rollback.
ARG FAIL_READINESS=false
ENV FAIL_READINESS=${FAIL_READINESS}

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
