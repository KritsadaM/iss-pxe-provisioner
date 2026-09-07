FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    qemu-system-x86 qemu-utils ipxe iproute2 iptables dnsmasq \
    openssl libarchive-tools cpio zstd novnc websockify ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "lab/router.py"]
