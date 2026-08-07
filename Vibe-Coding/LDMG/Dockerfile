FROM python:3.12-slim

# 安装 Docker CLI（修复 "docker: not found" 问题）
RUN apt-get update && apt-get install -y \
    docker.io \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .

# 启动时自动运行 bot
CMD ["python3", "bot.py"]
