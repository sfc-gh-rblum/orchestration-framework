FROM python:3.11-slim-bookworm

WORKDIR /app

# Install curl for healthcheck and clean up
RUN apt-get update && \
    apt-get install -y curl git && \
    rm -rf /var/lib/apt/lists/*

# Copy the entire repository first
COPY . .

# Install agent_gateway package in editable mode
RUN pip install -e .

# Install API dependencies
WORKDIR /app/flask_api
RUN pip install --no-cache-dir -r requirements.txt

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Environment variables for Snowflake connection
ENV SNOWFLAKE_ACCOUNT=sfsenorthamerica-demo175 \
    SNOWFLAKE_DATABASE=SPCS_OF \
    SNOWFLAKE_SCHEMA=SPCS_SCHEMA \
    SNOWFLAKE_WAREHOUSE=DEMO_COMPUTE_WH

# Use hypercorn for production deployment
CMD ["hypercorn", "--bind", "0.0.0.0:8080", "--workers", "4", "app:app"]
