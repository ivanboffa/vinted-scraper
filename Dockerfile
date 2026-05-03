FROM python:3.11-slim

# Install system deps needed by curl_cffi (native TLS libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Force unbuffered output so logs appear in real-time
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "main.py", "scheduler"]
