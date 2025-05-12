FROM python:3.11-slim-bookworm

WORKDIR /app

# Install curl for healthcheck and clean up
RUN apt-get update && \
    apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY flask_api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
RUN chmod -R 755 /app

WORKDIR /app/flask_api

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Use hypercorn for production deployment
CMD ["hypercorn", "--bind", "0.0.0.0:5000", "--workers", "4", "app:app"]
