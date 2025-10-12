FROM fuzzilli:latest

# Install Python and Redis dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install --break-system-packages redis asyncpg

# Copy integration scripts
COPY sync.py .
COPY fuzzilli_redis_integration.py .
COPY redis_producer.py .
COPY requirements.txt .

# Set environment variables
ENV REDIS_URL=redis://redis:6379
ENV FUZZER_ID=1

# Create corpus directory
RUN mkdir -p ./Corpus

# Expose Redis port
EXPOSE 6379

# Default command (can be overridden in docker-compose)
CMD ["python3", "fuzzilli_redis_integration.py"]