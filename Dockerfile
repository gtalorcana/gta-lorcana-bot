# Dockerfile — GTA Lorcana Discord Bot
# Lightweight Python image, no HTTP server needed.

FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot.py .

# Run the bot
CMD ["python", "-u", "bot.py"]