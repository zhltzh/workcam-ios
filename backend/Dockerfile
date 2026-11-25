# WorkCam verification service - local container image.
# Build context must be the backend/ directory:
#   docker build -t workcam-verify:local .
# NOT built/verified on this machine (no Docker Desktop installed) -
# see docs/deploy/local-deployment.md for the verification status.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKCAM_BUSINESS_DB=/data/business/workcam.db \
    WORKCAM_ENV=production

WORKDIR /app

COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY app ./app

RUN mkdir -p /data/business

EXPOSE 8000

# Binds 0.0.0.0 INSIDE the container network namespace only; the host
# side must map this port on loopback (see docker-compose.yml:
# "127.0.0.1:8000:8000"). NEVER publish this port on 0.0.0.0 on the
# host, and NEVER expose this service to the public internet.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
