FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/review.db \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 持久化 SQLite 数据的卷；启动时自动建表初始化
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 容器内固定监听 8000；宿主机映射端口由 docker-compose 的 PORT 控制
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port 8000"]
