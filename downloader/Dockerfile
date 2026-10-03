FROM denoland/deno:bin-2.5.2 AS deno
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_STATE_DIR=/app/downloader/state \
    YTDLP_COOKIES_FILE=/app/downloader/state/cookies.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY downloader/requirements.txt /app/downloader/requirements.txt
RUN pip install --no-cache-dir -r /app/downloader/requirements.txt
COPY downloader /app/downloader

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "downloader.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
