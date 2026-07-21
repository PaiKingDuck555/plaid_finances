FROM python:3.12-slim

# Tailscale needs curl + ca-certs to install; iproute2 is handy for debugging.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates iproute2 \
 && curl -fsSL https://tailscale.com/install.sh | sh \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

ENV DATABASE_PATH=/data/transactions.db

CMD ["./start.sh"]
