# Systemd Service Files

These are sample systemd service files for running scdlbot in production.

## Services

1. **scdlbot.service** - Main Telegram bot service
2. **scdlbot-ffprobe.service** - Huey worker for ffprobe operations
3. **scdlbot-ffmpeg.service** - Huey worker for ffmpeg operations (video conversion, file splitting)

## Installation

1. Copy the service files to systemd directory:
```bash
sudo cp systemd-sample/*.service /etc/systemd/system/
```

2. Create environment file:
```bash
sudo tee /etc/default/scdlbot << EOF
TG_BOT_TOKEN=your_bot_token_here
DL_MODE=audio
DL_TIMEOUT=900
FFMPEG_HUEY_WORKERS=2
LOG_LEVEL=INFO
EOF
```

3. Reload systemd and start services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable scdlbot scdlbot-ffprobe scdlbot-ffmpeg
sudo systemctl start scdlbot scdlbot-ffprobe scdlbot-ffmpeg
```

## Configuration

The ffmpeg worker uses `FFMPEG_HUEY_WORKERS` environment variable to control concurrency (default: 2).

## Monitoring

Check service status:
```bash
sudo systemctl status scdlbot
sudo systemctl status scdlbot-ffprobe
sudo systemctl status scdlbot-ffmpeg
```

View logs:
```bash
sudo journalctl -u scdlbot -f
sudo journalctl -u scdlbot-ffprobe -f
sudo journalctl -u scdlbot-ffmpeg -f
```