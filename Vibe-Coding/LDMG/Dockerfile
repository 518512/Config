FROM python:3.12-slim

# 安装 Docker CLI（修复 "docker: not found" 问题）
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    python3-pip \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y \
       docker-ce-cli \
       docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .

# 启动时自动运行 bot
CMD ["python3", "bot.py"]
