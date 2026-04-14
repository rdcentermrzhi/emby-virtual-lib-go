FROM node:20-alpine AS web-builder
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

COPY admin/requirements.txt ./admin/requirements.txt
RUN pip install --no-cache-dir -r ./admin/requirements.txt

COPY admin/ ./admin/
COPY --from=web-builder /src/web/dist ./web/dist

RUN mkdir -p /app/config /app/images

EXPOSE 8011
ENTRYPOINT ["python", "-m", "uvicorn", "admin.admin_server:admin_app", "--host", "0.0.0.0", "--port", "8011"]
