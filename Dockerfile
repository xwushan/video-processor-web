FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIDEO_PROCESSOR_ROOT=/data/video-processor

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /data/video-processor \
    && chown app:app /data/video-processor

COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

COPY web_app ./web_app
COPY processVideo.py rt.png dt.png ./

USER app
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8899/health', timeout=3)" || exit 1

CMD ["uvicorn", "web_app.main:app", "--host", "0.0.0.0", "--port", "8899", "--workers", "1"]

FROM base AS test
USER root
RUN pip install --no-cache-dir httpx==0.28.1
COPY tests ./tests
USER app
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="video-processor-web" \
    org.opencontainers.image.source="https://github.com/xwushan/video-processor-web" \
    org.opencontainers.image.version="1.3.5" \
    org.opencontainers.image.revision=$VCS_REF
ENV VIDEO_PROCESSOR_REVISION=$VCS_REF
