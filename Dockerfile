FROM python:3.10.8-slim-bullseye

WORKDIR /Jisshu-filter-bot
RUN chmod 777 /Jisshu-filter-bot

RUN apt update && apt install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
