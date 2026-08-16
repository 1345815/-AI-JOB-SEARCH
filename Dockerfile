FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY web ./web
COPY templates/profile_template.json ./templates/profile_template.json

RUN mkdir -p /app/web/data

EXPOSE 8000
VOLUME ["/app/web/data"]

CMD ["python", "web/server.py"]
