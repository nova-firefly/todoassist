FROM python:3.12-alpine

RUN apk add --no-cache curl \
 && addgroup -S -g 1000 app \
 && adduser -S -u 1000 -G app app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Pre-create /data as app:app so a first-mount named volume inherits that
# ownership. Runs the container non-root from the start, which means no
# CAP_CHOWN is needed at startup and cap_drop: ALL works cleanly.
RUN mkdir -p /data && chown -R app:app /data /app

ENV DATA_DIR=/data \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=15s \
  CMD wget -qO- http://127.0.0.1:8000/healthz >/dev/null || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=* --log-level $(echo ${LOG_LEVEL:-INFO} | tr '[:upper:]' '[:lower:]')"]
