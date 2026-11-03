# docker build -t scdlbot .
# docker run -d --name scdlbot --env-file .env -v scdlbot-data:/data -p 127.0.0.1:8000:8000 scdlbot

FROM python:3.15-slim AS build
# gcc: some dependencies (brotli) may not have wheels for the newest Python yet
RUN apt-get update && apt-get install -y --no-install-recommends git gcc libc6-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir poetry
WORKDIR /app
COPY pyproject.toml poetry.lock poetry.toml README.md ./
RUN poetry install --only main --no-root --no-interaction
COPY scdlbot ./scdlbot
RUN poetry install --only main --no-interaction

FROM python:3.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 scdlbot && mkdir /data && chown scdlbot /data
# JS runtime yt-dlp needs for YouTube:
COPY --from=denoland/deno:bin-2.9.7 /deno /usr/local/bin/deno
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH \
    DB_PATH=/data/scdlbot.sqlite3 \
    DL_DIR=/tmp/scdlbot \
    METRICS_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
USER scdlbot
VOLUME /data
EXPOSE 8000
CMD ["scdlbot"]
