# Argus - production-style image (non-root, no build tools at runtime)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ARGUS_RUNTIME_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY argus ./argus
RUN pip install ".[ai]" \
 && useradd --create-home --uid 10001 argus \
 && mkdir -p /data && chown argus:argus /data

USER argus
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "argus.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
