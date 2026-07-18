FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CMS_ENV=production \
    CMS_DATABASE=/data/cms.sqlite3 \
    CMS_MEDIA_DIR=/data/media \
    CMS_LEGACY_CRAWL=/data/legacy-crawl-checkpoint.json \
    CMS_MEDIA_MANIFEST=/data/legacy-media-manifest.json

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "jinja2>=3.1,<4" "python-multipart>=0.0.20,<1" "tzdata>=2025.2" "uvicorn[standard]>=0.32,<1"

COPY server ./server
COPY site ./site
COPY current-sections.json ./current-sections.json
RUN mkdir -p /data/media

EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
