# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install system dependencies: masscan, zmap, build tools, postgres client libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    masscan \
    zmap \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure scanner modules are importable
ENV PYTHONPATH=/app

# Default command (overridden in docker-compose)
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
