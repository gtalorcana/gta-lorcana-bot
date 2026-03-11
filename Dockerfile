FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py .
COPY results.py .
COPY stores.py .
COPY clients.py .
COPY constants.py .
COPY util/ ./util/
COPY scripts/ ./scripts/

CMD ["python", "bot.py"]