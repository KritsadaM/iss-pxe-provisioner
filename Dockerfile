FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends cpio zstd dnsmasq ipxe libarchive-tools openssl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PXE_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8090
# No access log: seed URLs contain short-lived deployment credentials.
CMD ["gunicorn", "--workers=1", "--threads=4", "--bind=0.0.0.0:8090", "portal:create_app()"]
