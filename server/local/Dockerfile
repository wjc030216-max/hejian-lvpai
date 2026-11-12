FROM python:3.11-slim
WORKDIR /app
COPY server/local/requirements-real.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ARG BUILD_REVISION=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.title="hejian-lvpai-local-runtime"
COPY server/common /app/server/common
COPY server/admin /app/server/admin
COPY server/file /app/server/file
COPY server/personattr /app/server/personattr
COPY server/charactergen /app/server/charactergen
COPY server/ai2pic/seedream_app.py /app/server/ai2pic/seedream_app.py
COPY server/aitask/seeddance_video_app.py /app/server/aitask/seeddance_video_app.py
COPY server/phase2 /app/server/phase2
COPY server/local /app/server/local
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/server
