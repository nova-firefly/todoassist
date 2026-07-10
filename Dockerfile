FROM python:3.12-alpine

RUN apk add --no-cache su-exec curl \
 && addgroup -S -g 1000 app \
 && adduser -S -u 1000 -G app app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV DATA_DIR=/data \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=15s \
  CMD wget -qO- http://127.0.0.1:8000/healthz >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
