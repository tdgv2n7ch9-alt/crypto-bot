FROM python:3.11-slim
WORKDIR /app
# Владелец, P0 2026-07-21 (рецидив 40МБ/GitHub-422): shadow_engine.py
# переведён с REST Git Data API на нативный git push (journal/shadow_signals.json
# перерастал практический лимит размера тела запроса REST Blobs API задолго
# до официальных 100МБ) -- нужен git-бинарник в рантайм-образе.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
