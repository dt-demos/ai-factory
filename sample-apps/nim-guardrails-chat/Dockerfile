FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed by some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app.py .
COPY update_config.py .
COPY guardrails_config/ ./guardrails_config/
COPY src/ ./src/
COPY static/ ./static/

# Streamlit static file serving reads from ./static/ and serves at /app/static/
ENV STREAMLIT_SERVER_ENABLE_STATIC_SERVING=true

# Entrypoint: generate config.yml from template, then start Streamlit
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/docker-entrypoint.sh"]
