FROM --platform=linux/amd64 python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Install local package in editable mode
RUN pip install -e .

# Expose the port the app runs on
EXPOSE 8080

# Declare required environment variables
ENV FLASK_APP=app.py \
    PORT=8080 \
    FLASK_ENV=production \
    PYTHONUNBUFFERED=1

# Create a script to validate environment variables and start the application
RUN echo '#!/bin/sh\n\
echo "🔍 Checking required Snowflake environment variables..."\n\
required_vars="SNOWFLAKE_HOST SNOWFLAKE_ACCOUNT SNOWFLAKE_USER SNOWFLAKE_ROLE SNOWFLAKE_WAREHOUSE SNOWFLAKE_DATABASE SNOWFLAKE_SCHEMA"\n\
missing_vars=""\n\
for var in $required_vars; do\n\
  if [ -z "$(eval echo \$$var)" ]; then\n\
    missing_vars="$missing_vars\\n  - $var"\n\
  fi\n\
done\n\
\n\
if [ ! -z "$missing_vars" ]; then\n\
  echo "❌ Error: Missing required Snowflake environment variables:$missing_vars"\n\
  echo "\nPlease provide these environment variables when running the container:"\n\
  echo "docker run -e SNOWFLAKE_HOST=<value> -e SNOWFLAKE_ACCOUNT=<value> ... <image-name>"\n\
  exit 1\n\
fi\n\
\n\
# Check for OAuth token in SPCS environment\n\
if [ -f "/snowflake/session/token" ]; then\n\
  echo "✅ Found OAuth token for authentication"\n\
elif [ -f "/app/rsa_key.p8" ]; then\n\
  echo "✅ Found private key for JWT authentication"\n\
else\n\
  echo "⚠️  Warning: No OAuth token or private key found. Authentication may fail."\n\
fi\n\
\n\
echo "✅ All required environment variables are set"\n\
echo "🚀 Starting Flask application..."\n\
exec python app.py' > /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

# Run the application
ENTRYPOINT ["/app/docker-entrypoint.sh"]
