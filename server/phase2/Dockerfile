FROM python:3.11-slim
WORKDIR /app
COPY server/phase2/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
ARG BUILD_REVISION=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.title="hejian-lvpai-phase2-api"
COPY server/common /app/server/common
COPY server/phase2 /app/server/phase2
COPY server/admin /app/server/admin
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/server
CMD ["python", "/app/server/phase2/phase2_app.py"]
