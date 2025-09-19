FROM ubuntu:22.04

# Install system dependencies and Python
RUN apt-get update && \
    apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    git \
    ffmpeg \
    systemd \
    systemd-sysv \
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

# Create inline requirements files and install packages system-wide using the production installation method
# requirements1.txt - Install pip and scdlbot from local sources
RUN echo "pip" > /tmp/requirements1.txt && \
    echo "." >> /tmp/requirements1.txt && \
    python3.11 -m pip install --upgrade --force-reinstall --upgrade-strategy=eager -r /tmp/requirements1.txt

# requirements2.txt - Install yt-dlp
RUN echo "yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git@master" > /tmp/requirements2.txt && \
    python3.11 -m pip install --upgrade --force-reinstall --upgrade-strategy=eager -r /tmp/requirements2.txt

# Create systemd service file for main bot
RUN cat > /etc/systemd/system/scdlbot.service << 'EOF'
[Unit]
Description=scdlbot
After=network.target

[Service]
User=www-data
Group=www-data
Type=simple
EnvironmentFile=/etc/default/scdlbot
ExecStart=/usr/local/bin/scdlbot
WatchdogSec=180
NotifyAccess=all
Restart=always
RestartSec=5
CPUQuotaPeriodSec=1000ms
CPUQuota=320%
MemoryHigh=6700M
MemoryMax=7000M
TasksMax=infinity
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service file for Huey ffprobe worker
RUN cat > /etc/systemd/system/scdlbot-ffprobe.service << 'EOF'
[Unit]
Description=scdlbot ffprobe worker (Huey)
After=network.target

[Service]
User=www-data
Group=www-data
Type=simple
EnvironmentFile=/etc/default/scdlbot
ExecStart=/usr/local/bin/huey_consumer scdlbot.ffprobe.huey --workers=2 --worker-type=process
Restart=always
RestartSec=5
CPUQuotaPeriodSec=1000ms
CPUQuota=100%
MemoryHigh=1024M
MemoryMax=1536M
TasksMax=infinity
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

# Create a startup script without systemd
RUN cat > /usr/local/bin/start-scdlbot.sh << 'EOF'
#!/bin/bash
set -e

echo "Starting scdlbot without systemd..."
echo "  - Starting Huey ffprobe worker"
echo "  - Starting main bot process"

# Source environment variables if they exist
if [ -f /etc/default/scdlbot ]; then
    source /etc/default/scdlbot
fi

# Function to handle shutdown
cleanup() {
    echo "Shutting down..."
    kill $HUEY_PID $BOT_PID 2>/dev/null || true
    wait $HUEY_PID $BOT_PID 2>/dev/null || true
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Start Huey consumer in background
echo "Starting Huey ffprobe worker..."
su -s /bin/bash www-data -c "/usr/local/bin/huey_consumer scdlbot.ffprobe.huey --workers=2 --worker-type=process" &
HUEY_PID=$!
echo "Huey worker started with PID $HUEY_PID"

# Give Huey a moment to start
sleep 2

# Start the main bot
echo "Starting scdlbot main process..."
su -s /bin/bash www-data -c "/usr/local/bin/scdlbot" &
BOT_PID=$!
echo "Bot started with PID $BOT_PID"

# Wait for both processes
echo "Services running. Press Ctrl+C to stop."
wait $HUEY_PID $BOT_PID
EOF

RUN chmod +x /usr/local/bin/start-scdlbot.sh

# Create environment file (will be overridden by docker-compose or kubernetes)
RUN touch /etc/default/scdlbot

# Enable both systemd services
RUN systemctl enable scdlbot && \
    systemctl enable scdlbot-ffprobe

# Expose any necessary ports (adjust as needed)
EXPOSE 5000

# Use the startup script as entrypoint
ENTRYPOINT ["/usr/local/bin/start-scdlbot.sh"]

# Use standard stop signal
STOPSIGNAL SIGTERM

# Health check - verify both processes are running
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "huey_consumer.*scdlbot.ffprobe" && pgrep -f "/usr/local/bin/scdlbot" || exit 1