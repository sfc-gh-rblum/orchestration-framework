FROM python:3.11-slim

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
EXPOSE 5000

# Environment variables will be provided at runtime
ENV FLASK_APP=app.py
ENV FLASK_ENV=development

# Command to run the application
CMD ["python", "app.py"]
