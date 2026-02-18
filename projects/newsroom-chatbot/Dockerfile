FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY web ./web
COPY output ./output

EXPOSE 8787

CMD ["sh", "-c", "uvicorn newsroom_chatbot.app:app --host 0.0.0.0 --port ${PORT:-8787}"]
