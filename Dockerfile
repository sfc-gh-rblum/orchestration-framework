FROM --platform=linux/amd64 python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libssl-dev \
    pkg-config \
    python3-dev \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies with specific pip version known to work with older snowflake-connector
RUN pip install --upgrade pip==23.0.1 && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Install local package in editable mode
RUN pip install -e .

# Expose the port the app runs on
EXPOSE 8080

# Environment variables for production
ENV FLASK_APP=app.py \
    PORT=8080 \
    FLASK_ENV=production \
    PYTHONUNBUFFERED=1

# Create a script to validate environment variables and start the application
RUN echo '#!/bin/sh\n\
required_vars="SNOWFLAKE_HOST SNOWFLAKE_ACCOUNT SNOWFLAKE_USER SNOWFLAKE_PASSWORD SNOWFLAKE_ROLE SNOWFLAKE_WAREHOUSE SNOWFLAKE_DATABASE SNOWFLAKE_SCHEMA"\n\
for var in $required_vars; do\n\
  if [ -z "$(eval echo \$$var)" ]; then\n\
    echo "Error: Required environment variable $var is not set"\n\
    exit 1\n\
  fi\n\
done\n\
\n\
exec gunicorn --bind 0.0.0.0:8080 --workers 4 --timeout 120 app:app' > /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

# Run the application with Gunicorn
ENTRYPOINT ["/app/docker-entrypoint.sh"]
