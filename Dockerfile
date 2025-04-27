FROM python:3.10-slim

# 安装依赖
RUN apt-get update && \
    apt-get install -y wget gnupg libnss3 libatk-bridge2.0-0 libgtk-3-0 libxss1 libasound2 libgbm1 libxshmfence1 libxcomposite1 libxrandr2 libu2f-udev libatk1.0-0 libpangocairo-1.0-0 libpango-1.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# 安装 Playwright 浏览器
RUN python -m playwright install --with-deps

CMD ["python", "main.py", "list"]