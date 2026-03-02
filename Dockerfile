FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py .
COPY rph_util.py .
COPY constants.py .
COPY util/ ./util/

CMD ["python", "bot.py"]
