FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && addgroup --system bot \
    && adduser --system --ingroup bot bot

COPY --chown=bot:bot . .
RUN mkdir -p /app/data && chown -R bot:bot /app/data

USER bot

CMD ["python", "-m", "main"]
