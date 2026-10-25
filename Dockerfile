FROM python:3.13-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY ml ./ml
COPY reports ./reports
COPY scripts ./scripts

EXPOSE 8000
# Migrations run automatically on startup (app/main.py -> app.db.init_db).
# Hosting platforms pass the port to listen on in $PORT; locally it's 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
