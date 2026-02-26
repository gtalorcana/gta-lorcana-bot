# Dockerfile — GTA Lorcana Discord Bot
# Lightweight Python image, no HTTP server needed.

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot.py .

# Run the bot
CMD ["python", "bot.py"]
