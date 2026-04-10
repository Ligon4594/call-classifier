# C&R Call Classifier — Railway deployment
# Python 3.11 slim for small image size + fast cold starts.
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY run.py .

# Railway injects env vars at runtime — no .env needed in the image.
# The CMD is overridden by Railway's cron config, but this default
# lets you test locally with: docker run --env-file .env call-classifier
CMD ["python", "run.py", "--days", "7"]
