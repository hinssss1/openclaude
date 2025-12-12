FROM python:3.11-slim

WORKDIR /app

# 基础运行配置
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝代码
COPY . .

# 数据目录（用于持久化 account_pool.json）
RUN mkdir -p /data

EXPOSE 8000

# 支持通过环境变量覆盖端口/号池文件位置
ENV PORT=8000 \
    POOL_FILE=/data/account_pool.json

CMD ["sh", "-c", "python pool_server.py --host 0.0.0.0 --port ${PORT:-8000} --pool-file ${POOL_FILE:-/data/account_pool.json}"]

