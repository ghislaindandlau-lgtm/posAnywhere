# ---------------------------------------------------------------------------
# Container image for the posAnywhere.io backend (modular monolith).
# Build:  docker build -t posanywhere-backend .
# Run:    docker run -p 8000:8000 posanywhere-backend
# ---------------------------------------------------------------------------

# Slim official Python base keeps the image small.
FROM python:3.12-slim

# Don't write .pyc files and stream logs straight to the console.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so Docker can cache this layer between builds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY app ./app

# Cloud platforms inject the port via $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

# Start the ASGI server. Shell form lets $PORT expand at runtime.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
