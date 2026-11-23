# 학습·데이터 작업용 이미지 (compose의 worker). 저장소 루트에서 빌드한다:
#   docker compose run --rm worker python -m server.storage pull --dest ml/data/raw/mermaid_rides
#   docker compose run --rm worker python3 ml/realdata/build_real.py --source mermaid --root ml/data/raw/mermaid_rides
# 결과는 ml/out/runs/vNNN (호스트의 ml/out에 그대로 남는다)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# LightGBM은 OpenMP(libgomp)가 필요하다
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY ml/requirements.txt ml/requirements.txt
COPY server/requirements.txt server/requirements.txt
RUN pip install -r ml/requirements.txt -r server/requirements.txt

COPY protocol/ protocol/
COPY controller/ controller/
COPY server/ server/
COPY ml/ ml/

ENV MERMAID_DATA=/data
CMD ["python", "-m", "server.storage", "--help"]
