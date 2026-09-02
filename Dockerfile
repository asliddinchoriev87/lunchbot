FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/lunchbot.db

WORKDIR /app
COPY . /app
RUN mkdir -p /data

VOLUME ["/data"]
CMD ["python", "-m", "lunchbot.main"]
