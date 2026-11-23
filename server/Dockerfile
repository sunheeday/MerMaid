# 로그 서버 이미지. 저장소 루트에서 빌드한다 (protocol/ride_schema.sql을 같이 넣는다):
#   docker build -f server/Dockerfile -t mermaid-api .
# 보통은 docker compose up (compose.yaml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt

COPY protocol/ride_schema.sql protocol/ride_schema.sql
COPY server/ server/

# /data: incoming/ (받는 중), S3 캐시, 그리고 STORAGE_URL·DATABASE_URL이 비었을 때의 기본 저장소와 SQLite
RUN useradd --system --uid 10001 --no-create-home mermaid && mkdir -p /data && chown mermaid /data
USER mermaid
ENV MERMAID_DATA=/data
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"]

# 프로세스 하나. 앞에 HTTPS 프록시(ALB, nginx)가 붙으므로 X-Forwarded-* 를 믿는다
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
