FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY master/ ./master/

# Create directories for runtime data
RUN mkdir -p uploads chunks results

# Expose port
EXPOSE 8000

# Set environment variable for port
ENV PORT=8000

# Run the server
CMD ["python", "master/server.py"]
