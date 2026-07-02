# Waybill image. Build multi-arch or with --platform=linux/amd64 on Apple
# Silicon so the same image runs on EKS (amd64) nodes later.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-tracing.txt ./
# Core deps always; tracing deps included so the image supports OTEL_ENABLED=true.
# (Tracing still stays OFF at runtime unless OTEL_ENABLED is set.)
RUN pip install --no-cache-dir -r requirements.txt -r requirements-tracing.txt

COPY waybill ./waybill
COPY alembic.ini ./alembic.ini

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["python", "-m", "waybill.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
