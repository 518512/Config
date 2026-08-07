FROM python:3.12-slim

# 不需要安装 apk 或其他系统工具
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .

CMD ["python3", "bot.py"]
