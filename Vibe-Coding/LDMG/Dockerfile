FROM docker:27-cli

# 安装 Python
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .

CMD ["python3", "bot.py"]
