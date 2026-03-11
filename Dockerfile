FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app/

# Add current directory to Python path
ENV PYTHONPATH=/app

# Create non-root user
RUN useradd -m -u 1000 reliapi && chown -R reliapi:reliapi /app
USER reliapi

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/healthz', timeout=5)"

# Run application
CMD ["uvicorn", "reliapi.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
