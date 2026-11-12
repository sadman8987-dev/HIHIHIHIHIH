FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_INSTALL=/usr/local

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl ffmpeg tini unzip \
    && curl -fsSL https://deno.land/install.sh | sh -s v2.2.7 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /tmp/media-fetch-bot \
    && chown -R appuser:appuser /app /data /tmp/media-fetch-bot

USER appuser
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "mediabot.main"]
