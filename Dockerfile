FROM ubuntu:22.04

# Install system dependencies and Python
RUN apt-get update && \
    apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    git \
    ffmpeg \
    curl \
    wget \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default and ensure pip is installed for it
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    update-alternatives --set python3 /usr/bin/python3.11 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Create www-data user if it doesn't exist
RUN id -u www-data &>/dev/null || useradd -m -s /bin/bash www-data

# Create necessary directories
RUN mkdir -p /opt/pyenv/versions/3.11.5/bin && \
    mkdir -p /etc/default && \
    mkdir -p /app

# Upgrade pip system-wide
RUN python3.11 -m pip install --upgrade pip

# Copy the application source code
WORKDIR /app
COPY . /app/

# Install the package and its dependencies
# First install the dependencies from pyproject.toml
RUN python3.11 -m pip install --upgrade pip setuptools wheel

# Install scdlbot package in editable mode with all dependencies
RUN python3.11 -m pip install -e .

# Create a startup script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
echo "Starting scdlbot services..."\n\
echo "  - Starting Huey ffprobe worker"\n\
echo "  - Starting Huey ffmpeg worker"\n\
echo "  - Starting Huey download worker"\n\
echo "  - Starting main bot process"\n\
\n\
# Source environment variables if they exist\n\
if [ -f /etc/default/scdlbot ]; then\n\
    source /etc/default/scdlbot\n\
fi\n\
\n\
# Function to handle shutdown\n\
cleanup() {\n\
    echo "Shutting down..."\n\
    kill ${FFPROBE_PID:-} ${FFMPEG_PID:-} ${DOWNLOAD_PID:-} ${BOT_PID:-} 2>/dev/null || true\n\
    wait ${FFPROBE_PID:-} ${FFMPEG_PID:-} ${DOWNLOAD_PID:-} ${BOT_PID:-} 2>/dev/null || true\n\
    exit 0\n\
}\n\
\n\
# Set up signal handlers\n\
trap cleanup SIGTERM SIGINT\n\
\n\
# Start Huey ffprobe consumer in background\n\
echo "Starting Huey ffprobe worker..."\n\
su -s /bin/bash www-data -c "/usr/local/bin/huey_consumer scdlbot.ffprobe.huey --workers=2 --worker-type=process" &\n\
FFPROBE_PID=$!\n\
echo "Huey ffprobe worker started with PID $FFPROBE_PID"\n\
\n\
# Start Huey ffmpeg consumer in background\n\
echo "Starting Huey ffmpeg worker..."\n\
FFMPEG_WORKERS=${FFMPEG_HUEY_WORKERS:-2}\n\
su -s /bin/bash www-data -c "/usr/local/bin/huey_consumer scdlbot.ffmpeg_worker.huey --workers=$FFMPEG_WORKERS --worker-type=thread" &\n\
FFMPEG_PID=$!\n\
echo "Huey ffmpeg worker started with PID $FFMPEG_PID (workers=$FFMPEG_WORKERS)"\n\
\n\
# Start Huey download consumer in background\n\
echo "Starting Huey download worker..."\n\
DOWNLOAD_WORKERS=${DOWNLOAD_HUEY_WORKERS:-4}\n\
su -s /bin/bash www-data -c "/usr/local/bin/huey_consumer scdlbot.download_worker.huey --workers=$DOWNLOAD_WORKERS --worker-type=thread" &\n\
DOWNLOAD_PID=$!\n\
echo "Huey download worker started with PID $DOWNLOAD_PID (workers=$DOWNLOAD_WORKERS)"\n\
\n\
# Give Huey workers a moment to start\n\
sleep 2\n\
\n\
# Start the main bot\n\
echo "Starting scdlbot main process..."\n\
su -s /bin/bash www-data -c "/usr/local/bin/scdlbot" &\n\
BOT_PID=$!\n\
echo "Bot started with PID $BOT_PID"\n\
\n\
# Wait for all processes\n\
echo "Services running. Press Ctrl+C to stop."\n\
wait ${FFPROBE_PID:-} ${FFMPEG_PID:-} ${DOWNLOAD_PID:-} ${BOT_PID:-}' > /usr/local/bin/start-scdlbot.sh && \
    chmod +x /usr/local/bin/start-scdlbot.sh

# Create environment file (will be overridden by docker-compose or kubernetes)
RUN touch /etc/default/scdlbot

# Expose any necessary ports (adjust as needed)
EXPOSE 5000

# Use the startup script as entrypoint
ENTRYPOINT ["/usr/local/bin/start-scdlbot.sh"]

# Use standard stop signal
STOPSIGNAL SIGTERM

# Health check - verify all processes are running
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "huey_consumer.*scdlbot.ffprobe" && \
        pgrep -f "huey_consumer.*scdlbot.ffmpeg_worker" && \
        pgrep -f "huey_consumer.*scdlbot.download_worker" && \
        pgrep -f "/usr/local/bin/scdlbot" || exit 1
