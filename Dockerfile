FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BRANDDECK_WORKSPACE=/data

WORKDIR /app
COPY . /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-impress fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ".[api,export]"

VOLUME ["/data"]
EXPOSE 8000
CMD ["branddeck", "serve", "--host", "0.0.0.0", "--port", "8000"]

